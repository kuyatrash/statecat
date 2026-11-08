#!/usr/bin/env python3
"""StateCat v1.3.0: one living project state, readable by any agent in one read.

This is a single, dependency-free Python script (Python 3.8+, UTF-8).
It makes no network calls and sends nothing anywhere. All data stays
in the project folder: STATE.md, STATE-ARCHIVE.md, and paired backups
in .statecat/backups/.

Normal output is banner-free and plain, one result per line, so agents
can parse it.

State file format (human-readable, human-editable):

    # Project: My Project
    > About: ...
    > Purpose: ...
    > Key paths: STATE.md, STATE-ARCHIVE.md, .statecat/backups/
    > Last ID used: #5

    ## Decided
    - #3 [2026-10-24] Use a local file, because the owner can read it.

    ## Open
    - #6 [2026-10-26] Do the next thing.

    ## Waiting on human
    - #5 [2026-10-26] Choose the final product name.

    ## Waiting on others
    - #7 [2026-10-26] Wait for the supplier reply.

    ## Parked
    - #4 [2026-10-20] Public launch - do not raise until reopened.

Rules implemented here (StateCat scope, revision 5):
- One plain-number global ID counter, stored in "Last ID used".
  An ID never changes and is never reused, even after archiving,
  superseding, moving buckets, or deleting the top item by hand.
  Allocation also scans both files and never issues a number found
  in either file.
- Decided items are replaced with `supersede`, not `update`:
  the replacement gets a new ID noting "supersedes #N" and the old
  item is archived under its original ID.
- Parked is soft protection, admitted as such. Moving an item out
  of Parked, archiving a parked item, or updating a parked item
  requires --owner-approved. `check --fix` is exempt: it only adds
  IDs and dates and never changes wording.
- Missing ID or missing date on a hand-added line is a warning,
  never a structural break and never a reason to block a write.
  `check --fix` (alias --fix-ids) assigns the next global ID and
  stamps an assigned date marked with an asterisk, e.g. [2026-10-26*].
  A starred date means age unknown: check never treats it as fresh
  and never uses it to flag or clear staleness.
- Every write is backed up first as a paired snapshot of both
  files. Two-file writes (supersede, archive) write the archive
  first, then STATE.md, each file atomically (temp file, then
  rename). A crash between the two writes leaves a duplicate ID;
  check reports it, naming the archive copy as the stray.
- `restore` restores both files together. Its default target is
  the most recent pre-write snapshot. A snapshot taken to protect
  a restore is a safety snapshot, listed as such, and is never the
  default target, so restoring twice does not flip back.
- Files are opened with newline="" on every platform, so untouched
  lines keep their original line endings byte-for-byte. The CLI
  never reformats, re-wraps, or reorders lines it is not changing.
- One writer at a time in v1. No cloud, no sync, no UI, no
  installer work, no task management beyond the five buckets.
"""

import argparse
import difflib
import json
import math
import os
import re
import shlex
import shutil
import sys
import tempfile
import threading
import time
import uuid
from datetime import date

VERSION = "1.3.0"

STATE = "STATE.md"
ARCHIVE = "STATE-ARCHIVE.md"
BACKUP_ROOT = os.path.join(".statecat", "backups")

BUCKETS = [
    "Decided",
    "Open",
    "Waiting on human",
    "Waiting on others",
    "Parked",
]
VALID_BUCKETS_TEXT = ", ".join(BUCKETS)


def unknown_bucket_message(bucket):
    """Clean error for an unknown bucket, listing the valid ones."""
    return "error: unknown bucket %r; valid buckets: %s\n" % (
        bucket,
        VALID_BUCKETS_TEXT,
    )
# Lowercase / short spellings accepted on the command line.
BUCKET_ALIASES = {
    "decided": "Decided",
    "open": "Open",
    "waiting on human": "Waiting on human",
    "waiting-on-human": "Waiting on human",
    "waiting_human": "Waiting on human",
    "human": "Waiting on human",
    "waiting on others": "Waiting on others",
    "waiting-on-others": "Waiting on others",
    "waiting_others": "Waiting on others",
    "others": "Waiting on others",
    "parked": "Parked",
}

SIZE_LINE_LIMIT = 150
SIZE_BYTE_LIMIT = 10 * 1024
STALE_DAYS = 14
BACKUPS_KEPT = 20

# One writer at a time, enforced with a lock directory. The lock is an
# atomic mkdir under .statecat/, so it works on Unix and Windows alike
# (no fcntl). A lock older than LOCK_STALE_SECONDS is treated as left
# behind by a crashed process and broken. Timeouts are overridable
# with STATECAT_LOCK_TIMEOUT / STATECAT_LOCK_STALE (seconds), mainly
# so tests can use short waits.
LOCK_DIRNAME = "lock"
LOCK_TIMEOUT_SECONDS = 10.0
LOCK_STALE_SECONDS = 60.0
LOCK_POLL_SECONDS = 0.05


def today_str():
    return date.today().isoformat()


def parse_id(text):
    """Accept '12' or '#12', return int or None."""
    if text is None:
        return None
    text = text.strip()
    if text.startswith("#"):
        text = text[1:]
    # ASCII digits only: str.isdigit() also accepts characters
    # like '²' that int() rejects, which used to crash the CLI.
    if not (text.isascii() and text.isdigit()):
        return None
    return _bounded_id(text)


# Item IDs are small counters, never huge numbers: cap the digit
# length so an over-long ID cannot crash the parser (Python
# 3.11+ caps int-string conversion, commonly at 4,300 digits).
MAX_ID_DIGITS = 10


def _bounded_id(digits):
    """int(digits) when the digit string is within the ID length
    cap, else None."""
    if len(digits) > MAX_ID_DIGITS:
        return None
    return int(digits)


def _valid_date(text):
    """True when text is a real calendar date, not just
    YYYY-MM-DD shaped. fromisoformat rejects impossible
    months and days (e.g. 2026-99-99) that the shape regex
    accepts."""
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return True


def split_ending(line):
    """Split a raw line (newline='' mode) into (content, ending)."""
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n") or line.endswith("\r"):
        return line[:-1], line[-1]
    return line, ""


class Item:
    """One '- ...' line in a state file."""

    def __init__(self, line_index, content, ending, bucket):
        self.line_index = line_index
        self.content = content
        self.ending = ending
        self.bucket = bucket  # bucket name, or "Archived" in the archive file
        self.item_id = None
        self.item_date = None
        self.date_invalid = False
        self.starred = False
        self.text = ""
        self.continuation_indices = []
        self.parse()

    def parse(self):
        body = self.content
        if not body.startswith("- "):
            return
        rest = body[2:]
        m = re.match(r"#([0-9]+) \[([0-9]{4}-[0-9]{2}-[0-9]{2})(\*)?\] ?(.*)$", rest)
        if m:
            self.item_id = _bounded_id(m.group(1))
            if self.item_id is None:
                self.text = rest
                return
            self.item_date = m.group(2)
            self.date_invalid = not _valid_date(self.item_date)
            self.starred = bool(m.group(3))
            self.text = m.group(4)
            return
        m = re.match(r"#([0-9]+) (.*)$", rest)
        if m:
            self.item_id = _bounded_id(m.group(1))
            if self.item_id is None:
                self.text = rest
                return
            self.text = m.group(2)
            return
        m = re.match(r"\[([0-9]{4}-[0-9]{2}-[0-9]{2})(\*)?\] ?(.*)$", rest)
        if m:
            self.item_date = m.group(1)
            self.date_invalid = not _valid_date(self.item_date)
            self.starred = bool(m.group(2))
            self.text = m.group(3)
            return
        self.text = rest

    def render(self):
        """Render this item back to a content line (no ending)."""
        if self.item_id is not None and self.item_date is not None:
            star = "*" if self.starred else ""
            return "- #%d [%s%s] %s" % (self.item_id, self.item_date, star, self.text)
        if self.item_id is not None:
            return "- #%d %s" % (self.item_id, self.text)
        if self.item_date is not None:
            star = "*" if self.starred else ""
            return "- [%s%s] %s" % (self.item_date, star, self.text)
        return "- %s" % self.text


class StateFile:
    """A parsed STATE.md or STATE-ARCHIVE.md, preserving raw lines."""

    def __init__(self, path):
        self.path = path
        self.lines = []  # list of [content, ending]
        self.items = []  # list of Item
        self.last_id = None
        self.last_id_index = None
        self.unknown_headings = []  # list of (line_index, heading)
        self.orphan_lines = []  # list of (line_index, content)
        self.stray_items = []  # list of (line_index, content): item-like lines outside any bucket
        self.duplicate_headings = []  # list of (line_index, heading)
        self.duplicate_last_id = []  # list of line_index: extra "> Last ID used:" lines
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8-sig", newline="") as handle:
                    raw = handle.readlines()
            except UnicodeDecodeError:
                # A file with invalid UTF-8 must fail cleanly, never
                # with a raw traceback, including for restore: the
                # caller decides the recovery, the user gets a
                # readable error.
                fail_cleanly(
                    "'%s' is not valid UTF-8; fix or remove the bad "
                    "bytes, or run statecat restore" % os.path.basename(path)
                )
            except OSError as exc:
                # A path that exists but cannot be read (a folder
                # named STATE.md, no read permission) must fail
                # cleanly too, never with a raw traceback.
                fail_cleanly(
                    "cannot read '%s' (%s); it must be a readable file"
                    % (os.path.basename(path), exc.strerror or exc)
                )
            for content, ending in (split_ending(line) for line in raw):
                self.lines.append([content, ending])
        self._parse()

    def _parse(self):
        bucket = None
        last_item = None
        in_fence = False
        seen_headings = set()
        for index, (content, _ending) in enumerate(self.lines):
            # Fenced code blocks are not structure: a heading or
            # list line inside ``` fences is an example, not a
            # bucket or an item. Toggle on fence lines and skip
            # everything inside, including the fences themselves.
            if content.lstrip().startswith("```"):
                in_fence = not in_fence
                last_item = None
                continue
            if in_fence:
                continue
            if content.startswith("> Last ID used:"):
                if self.last_id is not None:
                    self.duplicate_last_id.append(index)
                value = parse_id(content.split(":", 1)[1])
                if value is not None:
                    self.last_id = value
                    self.last_id_index = index
                continue
            if content.startswith("## "):
                heading = content[3:].strip()
                if self.path.endswith(ARCHIVE):
                    # Archive file: any section holds archived items.
                    if heading in seen_headings:
                        self.duplicate_headings.append((index, heading))
                    seen_headings.add(heading)
                    bucket = "Archived"
                elif heading in BUCKETS:
                    if heading in seen_headings:
                        self.duplicate_headings.append((index, heading))
                    seen_headings.add(heading)
                    bucket = heading
                else:
                    bucket = None
                    self.unknown_headings.append((index, heading))
                last_item = None
                continue
            if content.startswith("- "):
                if bucket is not None:
                    item = Item(index, content, self.lines[index][1], bucket)
                    self.items.append(item)
                    last_item = item
                else:
                    # An item-like line outside any bucket is
                    # invisible to show, ID allocation, and
                    # duplicate detection, so it must be
                    # flagged instead of silently ignored.
                    self.stray_items.append((index, content))
                    last_item = None
                continue
            if content.startswith((" ", "\t")) and bucket is not None:
                # An indented line immediately after an item (or after
                # that item's continuations) is part of that item, so
                # a hand-wrapped item moves and archives as one block.
                # Any other indented line is an orphan: it is not part
                # of an item, and check must flag it instead of
                # silently ignoring it.
                if (
                    last_item is not None
                    and index
                    == last_item.line_index + len(last_item.continuation_indices) + 1
                ):
                    last_item.continuation_indices.append(index)
                else:
                    self.orphan_lines.append((index, content))
                    last_item = None
                continue
            last_item = None

    def all_ids(self):
        return [item.item_id for item in self.items if item.item_id is not None]

    def find(self, item_id):
        for item in self.items:
            if item.item_id == item_id:
                return item
        return None

    def next_id(self, other):
        """Next global ID: Last ID used + 1, skipping any ID in use."""
        used = set(self.all_ids()) | set(other.all_ids())
        candidate = (self.last_id or 0) + 1
        while candidate in used:
            candidate += 1
        return candidate

    def set_last_id(self, value):
        """Update the Last ID used line in place, or insert it."""
        if self.last_id_index is not None:
            content = "> Last ID used: #%d" % value
            self.lines[self.last_id_index][0] = content
        else:
            # Insert after the About header lines, else at the top.
            insert_at = 0
            for index, (content, _ending) in enumerate(self.lines):
                if content.startswith("> "):
                    insert_at = index + 1
                elif content.startswith("## "):
                    break
            ending = self.lines[0][1] if self.lines else "\n"
            self.lines.insert(insert_at, ["> Last ID used: #%d" % value, ending])
            # Line indices of later items shift by one, including
            # their continuation lines, which are absolute indices.
            for item in self.items:
                if item.line_index >= insert_at:
                    item.line_index += 1
                item.continuation_indices = [
                    index + 1 if index >= insert_at else index
                    for index in item.continuation_indices
                ]
            self.last_id_index = insert_at
        self.last_id = value

    def dominant_ending(self):
        for _content, ending in self.lines:
            if ending:
                return ending
        return "\n"

    def bucket_range(self, bucket):
        """Return (start_index, end_index) for a bucket section.

        start_index is the heading line; end_index is the next heading
        line, or the end of the file. Headings inside fenced code
        blocks are examples, not structure, so they are skipped
        here just as in _parse.
        """
        start = None
        end = len(self.lines)
        in_fence = False
        for index, (content, _ending) in enumerate(self.lines):
            if content.lstrip().startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            # Match the heading the way _parse reads it (stripped), so a
            # trailing space an editor left on "## Open " does not make
            # the bucket "missing" while the parser sees it fine.
            if start is None and content.startswith("## ") and content[3:].strip() == bucket:
                start = index
            elif start is not None and content.startswith("## "):
                end = index
                break
        return start, end


def read_state_and_archive(directory):
    state = StateFile(os.path.join(directory, STATE))
    archive = StateFile(os.path.join(directory, ARCHIVE))
    return state, archive


def structural_problems(state, archive):
    """Return a list of structural problem strings (not warnings).

    Missing ID and missing date are warnings, not structural problems.
    """
    problems = []
    if not os.path.exists(state.path):
        problems.append("missing file '%s'" % STATE)
    for bucket in BUCKETS:
        if state.bucket_range(bucket)[0] is None:
            problems.append(
                "missing bucket heading '## %s' in %s" % (bucket, STATE)
            )
    for index, heading in state.unknown_headings:
        problems.append(
            "unknown bucket %r on line %d of %s" % (heading, index + 1, STATE)
        )
    for index, _content in state.orphan_lines:
        problems.append(
            "orphan indented line %d in %s is not part of an item; "
            "join it to the item above or remove the indent"
            % (index + 1, STATE)
        )
    for index, _content in state.stray_items:
        problems.append(
            "item-like line %d in %s is outside any bucket; "
            "move it under a bucket heading or delete it"
            % (index + 1, STATE)
        )
    for index, heading in state.duplicate_headings:
        problems.append(
            "duplicate bucket heading '## %s' on line %d of %s; "
            "keep one and move its items under it"
            % (heading, index + 1, STATE)
        )
    for index in state.duplicate_last_id:
        problems.append(
            "duplicate 'Last ID used' on line %d of %s; "
            "remove the extra line"
            % (index + 1, STATE)
        )
    if not os.path.exists(archive.path):
        problems.append(
            "missing file '%s'; restore a snapshot containing it, "
            "or recreate it from templates/%s" % (ARCHIVE, ARCHIVE)
        )
    elif archive.bucket_range("Archived")[0] is None:
        problems.append(
            "missing bucket heading '## Archived' in %s" % ARCHIVE
        )
    for index, _content in archive.orphan_lines:
        problems.append(
            "orphan indented line %d in %s is not part of an item; "
            "join it to the item above or remove the indent"
            % (index + 1, ARCHIVE)
        )
    for index, _content in archive.stray_items:
        problems.append(
            "item-like line %d in %s is outside any bucket; "
            "move it under a bucket heading or delete it"
            % (index + 1, ARCHIVE)
        )
    for index, heading in archive.duplicate_headings:
        problems.append(
            "duplicate bucket heading '## %s' on line %d of %s; "
            "keep one and move its items under it"
            % (heading, index + 1, ARCHIVE)
        )
    for index in archive.duplicate_last_id:
        problems.append(
            "duplicate 'Last ID used' on line %d of %s; "
            "remove the extra line"
            % (index + 1, ARCHIVE)
        )
    # Duplicate IDs: within a file, or across both files.
    locations = {}  # id -> list of (file, bucket, text)
    for source, parsed in ((STATE, state), (ARCHIVE, archive)):
        for item in parsed.items:
            if item.item_id is not None:
                locations.setdefault(item.item_id, []).append(
                    (source, item.bucket, item.text)
                )
    for item_id in sorted(locations):
        found = locations[item_id]
        if len(found) > 1:
            in_state = [loc for loc in found if loc[0] == STATE]
            in_archive = [loc for loc in found if loc[0] == ARCHIVE]
            if in_state and in_archive:
                state_text = in_state[0][2]
                archive_text = in_archive[0][2]
                if state_text == archive_text:
                    problems.append(
                        "duplicate ID #%d in %s and %s; "
                        "the %s copy is the stray, the %s copy is live"
                        % (item_id, STATE, ARCHIVE, ARCHIVE, STATE)
                    )
                else:
                    problems.append(
                        "duplicate ID #%d in %s and %s with different wording; "
                        "the stray cannot be determined automatically, "
                        "use paired restore" % (item_id, STATE, ARCHIVE)
                    )
            else:
                problems.append(
                    "duplicate ID #%d in %s" % (item_id, found[0][0])
                )
    return problems


def item_age_days(item, today=None):
    """Days since an item's date, or None when its age is unknown
    (no date, starred date, or unparseable date)."""
    if item.item_date is None or item.starred:
        return None
    try:
        year, month, day = (int(part) for part in item.item_date.split("-"))
        item_date = date(year, month, day)
    except ValueError:
        return None
    return ((today or date.today()) - item_date).days


def warnings_for(state, archive, directory=None):
    """Return a list of warning strings (never blocking)."""
    config = load_config(directory) if directory else dict(DEFAULT_CONFIG)
    warnings = []
    if state.last_id is None:
        warnings.append("warning: Last ID used line is missing in %s" % STATE)
    else:
        highest = 0
        for parsed in (state, archive):
            for item_id in parsed.all_ids():
                if item_id > highest:
                    highest = item_id
        if highest > state.last_id:
            warnings.append(
                "warning: Last ID used #%d is behind the highest ID in use #%d; "
                "run check --fix to raise it" % (state.last_id, highest)
            )
    for item in state.items:
        if item.item_id is None:
            warnings.append(
                "warning: line %d in %s (%s) has no ID"
                % (item.line_index + 1, STATE, item.bucket)
            )
        if item.date_invalid:
            warnings.append(
                "warning: #%s in %s has an invalid date [%s]; "
                "it is not a real calendar date, so its age is unknown"
                % (
                    item.item_id if item.item_id is not None else "?",
                    STATE,
                    item.item_date,
                )
            )
        if item.item_date is None:
            warnings.append(
                "warning: line %d in %s (%s) has no date"
                % (item.line_index + 1, STATE, item.bucket)
            )
        elif item.starred:
            warnings.append(
                "warning: #%d in %s has an assigned date [%s*] "
                "whose age is unknown, not fresh"
                % (item.item_id, STATE, item.item_date)
            )
    for item in archive.items:
        if item.item_id is None:
            warnings.append(
                "warning: line %d in %s has no ID"
                % (item.line_index + 1, ARCHIVE)
            )
        if item.date_invalid:
            warnings.append(
                "warning: #%s in %s has an invalid date [%s]; "
                "it is not a real calendar date, so its age is unknown"
                % (
                    item.item_id if item.item_id is not None else "?",
                    ARCHIVE,
                    item.item_date,
                )
            )
        if item.item_date is None:
            warnings.append(
                "warning: line %d in %s has no date"
                % (item.line_index + 1, ARCHIVE)
            )
    # Size budget.
    try:
        size = os.path.getsize(os.path.join(directory or os.getcwd(), STATE))
    except OSError:
        size = 0
    line_count = len(state.lines)
    if line_count > config["size_lines"] or size > config["size_bytes"]:
        warnings.append(
            "warning: %s is over the size budget (%d lines, %d bytes; "
            "budget %d lines / %d bytes)"
            % (STATE, line_count, size, config["size_lines"], config["size_bytes"])
        )
    # Stale waiting items: unstarred dates older than the configured days.
    today = date.today()
    for item in state.items:
        if item.bucket not in ("Waiting on human", "Waiting on others"):
            continue
        age = item_age_days(item, today)
        if age is None or age <= config["stale_days"]:
            continue
        if item.bucket == "Waiting on human":
            warnings.append(
                "stale: #%d in Waiting on human is %d days old "
                "(owner only, not a prompt to chase)"
                % (item.item_id, age)
            )
        else:
            warnings.append(
                "stale: #%d in Waiting on others is %d days old "
                "(not a prompt to chase)"
                % (item.item_id, age)
            )
    return warnings


def ensure_dir(path):
    """os.makedirs(path, exist_ok=True), but a file sitting where a
    folder must be (.statecat, .statecat/backups) fails cleanly
    with no write, never with a raw FileExistsError."""
    try:
        os.makedirs(path, exist_ok=True)
    except (FileExistsError, NotADirectoryError):
        fail_cleanly(
            "'%s' must be a folder, but a file is in the way; "
            "move or delete that file and try again" % path
        )


def fail_cleanly(message):
    sys.stderr.write("error: %s\n" % message)
    sys.stderr.write("No files were changed. Fix the problem line or run statecat restore.\n")
    sys.exit(1)


def _current_umask():
    """Current process umask, read without changing it.

    Linux: /proc/self/status reports it race-free. Anything
    else falls back to the os.umask round-trip (set it to its
    own value, which is atomic enough here: the heartbeat
    thread never touches the umask)."""
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("Umask:"):
                    return int(line.split(":", 1)[1].strip(), 8)
    except (OSError, ValueError):
        pass
    mask = os.umask(0)
    os.umask(mask)
    return mask


def _mode_for_write(path):
    """Permission bits for a file being (re)written at path.

    An existing target keeps its own bits (a 0600 state file
    stays 0600 through a rewrite, so a confidential file is
    never silently made world-readable). A new file gets the
    mode the umask allows (0666 & ~umask), never a forced
    0644. Only the rwx bits are preserved or set; setuid,
    setgid and sticky bits are never carried over."""
    try:
        return os.stat(path).st_mode & 0o777
    except OSError:
        return 0o666 & ~_current_umask()


def _write_target(path):
    """The real file to replace. A symlinked STATE.md is written
    through to its target; replacing the link itself would turn
    it into a regular file and silently stop updating the file it
    pointed to."""
    if os.path.islink(path):
        return os.path.realpath(path)
    return path


def atomic_write(path, lines):
    """Write lines ([content, ending] pairs) atomically: temp, then rename.

    The replacement keeps the target's existing permission bits
    when the target exists, and takes the umask's mode for a
    new file, so a restrictive file is never silently opened
    up by a rewrite."""
    path = _write_target(path)
    directory = os.path.dirname(path) or "."
    handle, tmp_path = tempfile.mkstemp(dir=directory, prefix=".statecat-tmp-")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as out:
            for content, ending in lines:
                out.write(content + ending)
        os.chmod(tmp_path, _mode_for_write(path))
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def atomic_write_bytes(path, data):
    """Write bytes atomically: temp, then rename. Same permission
    policy as atomic_write: keep the target's bits, umask for
    a new file."""
    path = _write_target(path)
    directory = os.path.dirname(path) or "."
    handle, tmp_path = tempfile.mkstemp(dir=directory, prefix=".statecat-tmp-")
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(data)
        os.chmod(tmp_path, _mode_for_write(path))
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def backup_root(directory):
    return os.path.join(directory, BACKUP_ROOT)


def _format_seconds(value):
    """A duration in whole seconds, e.g. 60 -> "60s"."""
    return "%ds" % int(round(value))


def _pid_is_alive(pid):
    """True if pid exists, False if definitely dead, None if unknown.

    On POSIX, os.kill(pid, 0) does not send a signal; it only
    checks existence. ProcessLookupError means dead,
    PermissionError means a process exists that we may not
    signal, and any other OSError means liveness cannot be
    established here. On Windows this probe must never run:
    signal.CTRL_C_EVENT is literally 0 there, so os.kill(pid, 0)
    would call GenerateConsoleCtrlEvent and send Ctrl+C to the
    process group instead of probing. Windows therefore always
    returns None (unknown): a Windows lock is only broken after
    the unknown-owner second threshold, ten times the stale
    limit, because its owner's liveness cannot be established.
    """
    if sys.platform == "win32":
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, ValueError, OverflowError):
        return None
    return True


def _process_start_time(pid):
    """Start time of a process as epoch seconds, or None if unknown.

    Linux only, via /proc: starttime is field 22 of
    /proc/<pid>/stat (clock ticks since boot); boot time is
    btime in /proc/stat. A PID alone cannot identify a
    process: after the owner dies, the OS may hand its PID to
    an unrelated process, and a liveness probe then reports
    the reused PID as alive forever. The start time tells
    the original owner from the reuse. Returns None on any
    failure, and on platforms without /proc (including
    Windows, where liveness is unknown anyway)."""
    if sys.platform == "win32":
        return None
    try:
        with open("/proc/%d/stat" % pid, "r", encoding="utf-8") as handle:
            stat = handle.read()
        # comm is in parens and may contain spaces or parens;
        # the fields after the last ')' start at field 3.
        rparen = stat.rfind(")")
        fields = stat[rparen + 2:].split()
        # fields[0] is state (field 3); starttime is field 22.
        start_ticks = int(fields[19])
        ticks_per_second = os.sysconf("SC_CLK_TCK")
        with open("/proc/stat", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("btime "):
                    boot = int(line.split()[1])
                    break
            else:
                return None
        return boot + start_ticks / ticks_per_second
    except (OSError, ValueError, IndexError):
        return None


# A lock whose owner cannot be determined -- no owner file yet,
# an unreadable owner file, or a liveness check that returns
# indeterminate -- is never abandoned in the normal stale
# window: unknown is not dead. It is only abandoned after this
# many multiples of the stale limit, a second threshold long
# enough that a writer merely paused between creating the lock
# directory and writing its owner file (milliseconds in normal
# operation) has either finished writing or is dead.
UNKNOWN_OWNER_STALE_MULTIPLIER = 10


# Env-override timing warnings are printed once per process, so a
# command that acquires the lock several times does not spam stderr.
_lock_timing_warning_printed = False


class project_lock:
    """Cross-platform write lock for one project, via atomic mkdir.

    Use as a context manager around a whole read-modify-write command:
    the lock is held from before the first read until after the last
    write, so two writers can no longer both read the same Last ID
    used, assign the same next ID, and silently lose one item.
    Read-only commands that report on both state files take the
    lock too, so a reader can never observe the half-finished
    pair left between the two writes of a supersede, archive,
    or compact.

    Ownership: acquiring the lock writes a random owner token
    (PID + uuid) into the lock's owner file. Release and stale
    removal never check-then-delete in place: they first rename
    the lock directory aside (atomic on one filesystem), then
    verify the token or the age on the renamed copy, and only
    delete it when the copy is still theirs / still stale. A
    copy that turns out to belong to someone else, or to be
    fresh, is put back if the lock path is free, and never
    deleted. That closes the check-then-delete race where a
    writer could read its own token, pause while another writer
    took over, and then delete the new owner's lock.

    Abandonment is proved, not assumed from age alone, and
    unknown is not dead. A lock whose owner PID is still alive
    is never deleted as stale, however old its directory looks,
    and while a lock is held a daemon heartbeat thread touches
    its directory mtime every few seconds, so a long-running
    but alive writer's lock stays fresh. A stale lock is
    therefore only broken when its directory is old *and* its
    owner is proved dead. A lock whose owner cannot be
    determined -- no owner file yet, an unreadable owner file,
    or a liveness check that returns indeterminate -- is not
    deleted in the normal stale window at all; it is only
    abandoned after ten times the stale limit, a second
    threshold long enough that a writer merely paused between
    creating the lock directory and writing its owner file
    (milliseconds in normal operation) has either finished
    writing or is dead. The owner file itself is created
    exclusively, so a writer whose directory was broken and
    replaced can never write its token into the new owner's
    lock: the exclusive create fails and it waits and retries
    instead. A writer that fails to write its owner file for
    any other reason deletes nothing: the directory at the
    lock path may already be a replacement writer's lock, and
    no check can prove it is still ours, so it is left for
    stale cleanup and the writer fails cleanly. A genuinely
    own directory left behind strands the project until it
    ages past the unknown-owner second threshold, because it
    has no owner file to prove its owner dead.

    Residual limitation, stated honestly: the rename is atomic,
    but the verify-and-delete/restore that follows is not one
    operation. If a third writer creates a lock directory in
    the tiny window between the existence check in
    _restore_trash and the rename, the restore is declined
    (the trash dir is left for later stale cleanup) rather
    than overwriting it, but that check and the rename are
    still two steps, so a mkdir landing exactly between them
    could still be replaced on POSIX. A crash in that same
    window leaves the same kind of leftover, never a deleted
    live lock. A PID recorded in the owner file can be reused
    by an unrelated process after the owner dies, but the
    owner file also records the owner's process start time,
    and the staleness check compares it with the start time
    of the process now holding that PID: a mismatch proves
    the PID was reused and the owner is dead, so the lock is
    broken instead of stranded. The start time comes from
    /proc, so this only works on Linux; where it is
    unavailable (including Windows, where liveness is always
    unknown) the check falls back to the PID-only probe, and
    a reused PID there still strands the lock until that
    unrelated process exits. The heartbeat does not fix
    that fallback case -- the original owner's heartbeat
    thread died with it, and the unrelated process never
    touches the lock's mtime. The second threshold for unknown
    ownership is likewise not a proof: a writer suspended
    for longer than ten times the stale limit between mkdir
    and its owner write could still lose its lock, and the
    exclusive owner-file create is what keeps that case
    from corrupting the replacement owner's lock. On Windows
    the PID liveness probe is never run at all: signal.CTRL_C_EVENT
    is literally 0 there, so os.kill(pid, 0) would send Ctrl+C to
    the process group instead of probing, so Windows liveness is
    always unknown and a Windows lock is only broken after the
    ten-times stale limit for unknown ownership.
    """

    def __init__(self, directory):
        self.directory = directory
        self.lock_path = os.path.join(directory, ".statecat", LOCK_DIRNAME)
        self.acquired = False
        self.owner_token = None
        self.token_written = False
        self._heartbeat_stop = None
        self._heartbeat_thread = None
        self._heartbeat_interval = 10.0

    def _read_owner_pid_from(self, directory):
        """Owner PID from a lock dir's owner file, or None."""
        try:
            with open(os.path.join(directory, "owner"), "r", encoding="utf-8") as handle:
                for line in handle:
                    if line.startswith("pid="):
                        # The line is "pid=<n> time=<stamp>".
                        return int(line.split("=", 1)[1].strip().split()[0])
        except (OSError, ValueError, IndexError):
            pass
        return None

    def _read_owner_start_from(self, directory):
        """Owner process start time (epoch seconds) from a lock
        dir's owner file, or None when it is missing, "unknown",
        or unparsable."""
        try:
            with open(os.path.join(directory, "owner"), "r", encoding="utf-8") as handle:
                for line in handle:
                    if line.startswith("start="):
                        value = line.split("=", 1)[1].strip()
                        if value == "unknown":
                            return None
                        return float(value)
        except (OSError, ValueError, IndexError):
            pass
        return None

    def _owner_is_alive(self, directory):
        """True if the lock owner is a live process, False if
        proved dead, None if liveness is unknown.

        A PID alone is not proof: after the owner dies, the OS
        may reuse its PID for an unrelated process, and a plain
        liveness probe then reports the reused PID as alive
        forever. When the owner file records a start time and
        the process now holding that PID started at a different
        time, the PID was reused and the owner is dead. When
        either start time is unavailable, this falls back to
        the PID-only probe (the old behaviour)."""
        pid = self._read_owner_pid_from(directory)
        if pid is None:
            return None
        alive = _pid_is_alive(pid)
        if alive is not True:
            return alive
        recorded = self._read_owner_start_from(directory)
        if recorded is None:
            return True
        current = _process_start_time(pid)
        if current is None:
            return True
        return current == recorded

    def _is_abandoned_dir(self, directory, stale):
        """True only when abandonment is proved, never on unknown ownership.

        Unknown is not dead. A lock whose owner PID cannot be
        determined, or whose liveness cannot be established,
        is never deleted in the normal stale window: it may
        belong to a writer paused between mkdir and writing
        its owner file, and deleting it would let that writer
        resume into a new owner's lock. Such a lock is only
        abandoned after a much longer second threshold
        (UNKNOWN_OWNER_STALE_MULTIPLIER times the stale
        limit), by which time a writer merely paused between
        mkdir and its owner write -- a gap of milliseconds
        in normal operation -- has either finished writing
        or is dead. A lock with a recorded, definitely dead
        owner is abandoned at the normal stale limit; a live
        owner is never abandoned, however old the lock looks.
        """
        if not self._is_stale_dir(directory, stale):
            return False
        alive = self._owner_is_alive(directory)
        if alive is True:
            return False
        if alive is None:
            return self._is_stale_dir(
                directory, stale * UNKNOWN_OWNER_STALE_MULTIPLIER
            )
        return True

    def _heartbeat_loop(self):
        """Touch the lock dir mtime while this lock is held.

        Runs in a daemon thread, stopped in __exit__. It only
        touches the directory while the token on disk still
        matches ours. The token check and the touch are two
        separate steps, so a takeover landing between them
        could make it touch a successor's directory once; it
        never deletes or replaces anything, it only refreshes
        an mtime, and the thread stops on the first such
        mismatch or error.
        """
        while not self._heartbeat_stop.wait(self._heartbeat_interval):
            if not self.acquired:
                return
            try:
                if self._read_owner_token() != self.owner_token:
                    return
                os.utime(self.lock_path, None)
            except OSError:
                return

    def _start_heartbeat(self, stale):
        """Start the heartbeat thread; interval scales with stale."""
        self._heartbeat_interval = min(10.0, max(1.0, stale / 6.0))
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop, daemon=True
        )
        self._heartbeat_thread.start()

    def _stop_heartbeat(self):
        """Stop the heartbeat thread, if one is running."""
        if self._heartbeat_thread is not None:
            self._heartbeat_stop.set()
            self._heartbeat_thread.join(timeout=2.0)
            self._heartbeat_thread = None
            self._heartbeat_stop = None

    def _lock_age(self):
        try:
            return time.time() - os.path.getmtime(self.lock_path)
        except OSError:
            return 0.0

    def _manual_lock_clear_command(self):
        """Shell command that removes this lock dir by hand.

        POSIX: rm -rf with the path shell-quoted, so a project
        path containing spaces or shell metacharacters pastes
        safely. Windows: rm -rf is not a Windows command, so
        give the PowerShell equivalent instead, with the path
        single-quoted (a single quote in the path is doubled,
        which is PowerShell's escape). Use -LiteralPath, not
        -Path: -Path still interprets wildcards, so a path with
        square brackets could match the wrong thing.
        """
        if sys.platform == "win32":
            quoted = "'" + self.lock_path.replace("'", "''") + "'"
            return "Remove-Item -LiteralPath %s -Recurse -Force" % quoted
        return "rm -rf %s" % shlex.quote(self.lock_path)

    def _lock_held_message(self, stale):
        """Explain a lock that outlasted our wait, with numbers.

        Names the owner PID (or "unknown"), the lock age, the
        stale limit in use, and when the lock is expected to
        clear under the same rules _is_abandoned_dir applies:
        a proved-dead owner clears once the lock ages past
        `stale`; an unknown owner (no owner file, or liveness
        indeterminate, including always on Windows) clears once
        it ages past ten times `stale`; a live owner never
        clears while it is alive. Also says how to clear the
        lock by hand, for when no writer is actually running.

        The opening is worded per ownership state: a dead
        owner is not "another process writing" -- no process
        is writing, the lock was left behind. A live owner
        keeps the writing wording, and an unknown owner stays
        neutral. When the remaining time is under a second,
        the message says the lock should clear on the next
        attempt instead of promising "in about 0 seconds":
        int() would truncate a sub-second remainder to zero,
        and the deadline is checked before another stale
        break is attempted, so this attempt exits without
        removing it and the next attempt does.
        """
        pid = self._read_owner_pid_from(self.lock_path)
        age = self._lock_age()
        pid_text = str(pid) if pid is not None else "unknown"
        if pid is None:
            alive = None
        else:
            alive = self._owner_is_alive(self.lock_path)
        if alive is True:
            opening = (
                "another statecat process is writing in this project; "
                "the lock is held"
            )
            clears = (
                "never while the owner is alive "
                "(owner PID %s is still running)" % pid
            )
        elif alive is False:
            opening = (
                "the lock was left behind by a statecat process that "
                "is no longer running; no process is writing in this "
                "project"
            )
            remaining = stale - age
            if remaining < 0:
                # Already past the threshold: the deadline is
                # checked before another stale break is attempted,
                # so this attempt exits without removing the lock
                # and the next attempt does the cleanup. Do not
                # claim the limit is "less than a second away".
                clears = (
                    "on the next attempt, once the lock ages past the %s stale limit "
                    "(the lock is already past the stale limit; try again now)"
                    % _format_seconds(stale)
                )
            elif remaining < 1.0:
                # Less than a second left would print as "in about
                # 0 seconds" (int() truncates), and this attempt
                # exits without removing the lock anyway: the
                # deadline is checked before another stale break
                # is attempted, so the next attempt does the
                # cleanup. Say that instead of promising zero.
                clears = (
                    "on the next attempt, once the lock ages past the %s stale limit "
                    "(the stale limit is less than a second away; try again now)"
                    % _format_seconds(stale)
                )
            else:
                clears = (
                    "in about %d seconds, once the lock ages past the %s stale limit "
                    "(the owner is dead)"
                    % (int(remaining), _format_seconds(stale))
                )
        else:
            opening = (
                "the lock is held by a statecat process whose owner "
                "cannot be determined; no process is known to be writing"
            )
            limit = stale * UNKNOWN_OWNER_STALE_MULTIPLIER
            remaining = limit - age
            if remaining < 0:
                clears = (
                    "on the next attempt, once the lock ages past the %s unknown-owner limit "
                    "(10x the %s stale limit; the lock is already past the limit; try again now)"
                    % (_format_seconds(limit), _format_seconds(stale))
                )
            elif remaining < 1.0:
                clears = (
                    "on the next attempt, once the lock ages past the %s unknown-owner limit "
                    "(10x the %s stale limit; the limit is less than a second away; try again now)"
                    % (_format_seconds(limit), _format_seconds(stale))
                )
            else:
                clears = (
                    "in about %d seconds, once the lock ages past the %s unknown-owner limit "
                    "(10x the %s stale limit; the owner cannot be determined)"
                    % (int(remaining), _format_seconds(limit), _format_seconds(stale))
                )
        return (
            "error: %s (owner PID %s, lock age %s, stale limit %s).\n"
            "The lock should clear %s.\n"
            "If no writer is actually running, remove the lock directory: %s\n"
            % (opening, pid_text, _format_seconds(age), _format_seconds(stale), clears, self._manual_lock_clear_command())
        )

    def _read_owner_token_from(self, directory):
        try:
            with open(os.path.join(directory, "owner"), "r", encoding="utf-8") as handle:
                for line in handle:
                    if line.startswith("token="):
                        return line.split("=", 1)[1].strip()
        except OSError:
            pass
        return None

    def _read_owner_token(self):
        return self._read_owner_token_from(self.lock_path)

    def _owns_lock(self):
        return self.owner_token is not None and self._read_owner_token() == self.owner_token

    def _trash_path(self):
        """Unique sibling path for a lock dir moved out of the way."""
        return "%s.gone-%d-%s" % (self.lock_path, os.getpid(), uuid.uuid4().hex)

    def _rename_lock_to_trash(self):
        """Atomically move the lock dir aside; return its new path or None.

        os.rename is atomic on one filesystem, so the dir is either
        still at lock_path or wholly at the trash path, never half
        moved. Returns None when the lock dir is already gone.
        """
        trash = self._trash_path()
        try:
            os.rename(self.lock_path, trash)
        except OSError:
            return None
        return trash

    def _restore_trash(self, trash):
        """Move a renamed lock dir back, only if lock_path is free.

        Never renames over an existing lock_path: on POSIX
        os.rename can replace an existing *empty* directory,
        so an unchecked rename could destroy a third writer's
        just-created lock. If lock_path already exists, the
        trash dir is left in place for later stale cleanup.
        Residual race, stated honestly: a mkdir can land in
        the tiny window between the lexists check and the
        rename; that window is a single call sequence, and
        the worst case is the same empty-dir replacement,
        which is why a declined restore is never an error.
        """
        if os.path.lexists(self.lock_path):
            return False
        try:
            os.rename(trash, self.lock_path)
        except OSError:
            return False
        return True

    def _delete_trash(self, trash):
        shutil.rmtree(trash, ignore_errors=True)

    def _is_stale_dir(self, directory, stale):
        """True when a lock dir's mtime is older than the stale limit."""
        try:
            return time.time() - os.path.getmtime(directory) > stale
        except OSError:
            return False

    def _cleanup_orphaned_lock_dirs(self, stale):
        """Remove lock dirs a crashed release left renamed aside.

        Only dirs _is_abandoned_dir judges abandoned are
        removed: a live writer's rename-then-verify window is
        milliseconds, so its trash dir is always fresh and
        never touched here, and a trash dir whose owner is
        unknown is held to the longer second threshold, for
        the same reason as a live lock (see _is_abandoned_dir).
        """
        parent = os.path.dirname(self.lock_path)
        prefix = LOCK_DIRNAME + ".gone-"
        try:
            names = os.listdir(parent)
        except OSError:
            return
        for name in names:
            if not name.startswith(prefix):
                continue
            path = os.path.join(parent, name)
            if os.path.isdir(path) and self._is_abandoned_dir(path, stale):
                shutil.rmtree(path, ignore_errors=True)

    def _resolve_lock_timing(self, config):
        """(timeout, stale) with env overrides fully range-checked.

        STATECAT_LOCK_TIMEOUT / STATECAT_LOCK_STALE override
        .statecat/config, which overrides the defaults, but an
        env value gets the same config_range_error bounds as
        `config set` and JSON (lock_timeout 0.1-3600,
        lock_stale 1-86400, finite only). An unparseable,
        non-finite, or out-of-range env value warns once per
        process and falls back to the config value, so a bad
        env var can never make a writer wait forever or break
        a live lock after a fraction of a second.

        The stale > timeout rule is warning-only here: env
        overrides are an operator/test escape hatch, and the
        test suite deliberately uses timeout 5 / stale 1 to
        make stale break quickly. A stale value that is not
        greater than the timeout warns, and the values are
        kept. The strict rule (reject) applies to `config
        set` and to JSON config, in cmd_config / load_config.
        """
        global _lock_timing_warning_printed
        values = {}
        warned = False
        for env_key, key in (
            ("STATECAT_LOCK_TIMEOUT", "lock_timeout"),
            ("STATECAT_LOCK_STALE", "lock_stale"),
        ):
            raw = os.environ.get(env_key)
            if raw is None:
                values[key] = config[key]
                continue
            try:
                value = float(raw)
            except ValueError:
                if not _lock_timing_warning_printed:
                    sys.stderr.write(
                        "warning: ignoring invalid %s %r; using %s\n"
                        % (env_key, raw, config[key])
                    )
                    warned = True
                values[key] = config[key]
                continue
            range_error = config_range_error(key, value)
            if range_error is not None:
                if not _lock_timing_warning_printed:
                    sys.stderr.write(
                        "warning: ignoring out-of-range %s %r (%s); using %s\n"
                        % (env_key, raw, range_error.strip(), config[key])
                    )
                    warned = True
                values[key] = config[key]
                continue
            values[key] = value
        timeout = values["lock_timeout"]
        stale = values["lock_stale"]
        if stale <= timeout and not _lock_timing_warning_printed:
            sys.stderr.write(
                "warning: lock_stale (%s) is not greater than "
                "lock_timeout (%s); a waiter can have its lock "
                "broken as stale\n" % (stale, timeout)
            )
            warned = True
        if warned:
            _lock_timing_warning_printed = True
        return timeout, stale

    def __enter__(self):
        # Timeout and staleness: STATECAT_LOCK_TIMEOUT / STATECAT_LOCK_STALE
        # (for tests) override .statecat/config, which overrides defaults.
        config = load_config(self.directory)
        timeout, stale = self._resolve_lock_timing(config)
        ensure_dir(os.path.dirname(self.lock_path))
        self._cleanup_orphaned_lock_dirs(stale)
        deadline = time.monotonic() + timeout
        while True:
            try:
                os.mkdir(self.lock_path)
            except FileExistsError:
                # Check the deadline first: a lock that is
                # restored (live owner, or fresh) must not spin
                # the rename/restore loop forever. A stale
                # break is only attempted while time remains.
                if time.monotonic() >= deadline:
                    sys.stderr.write(self._lock_held_message(stale))
                    sys.stderr.write("No files were changed.\n")
                    sys.exit(1)
                if self._lock_age() > stale:
                    trash = self._rename_lock_to_trash()
                    if trash is None:
                        continue
                    # Re-check on the renamed dir: a fresh lock
                    # may have been created between the age check
                    # and the rename, and a lock whose owner is
                    # still alive is never abandoned, however old
                    # it looks. Only a still-stale dir with a
                    # proved-dead owner -- or an unknown owner
                    # past the longer second threshold -- is
                    # deleted; any other copy is put back,
                    # never deleted.
                    if self._is_abandoned_dir(trash, stale):
                        self._delete_trash(trash)
                    else:
                        self._restore_trash(trash)
                        time.sleep(LOCK_POLL_SECONDS)
                    continue
                time.sleep(LOCK_POLL_SECONDS)
                continue
            except FileNotFoundError:
                ensure_dir(os.path.dirname(self.lock_path))
                continue
            # mkdir succeeded: write the owner file exclusively.
            # If we lost the race in the window between mkdir
            # and the write (another writer replaced our dir),
            # never touch the directory and never write into
            # it; wait and try again.
            if self._write_owner_file():
                self.acquired = True
                self._start_heartbeat(stale)
                return self
            time.sleep(LOCK_POLL_SECONDS)
            continue

    def _write_owner_file(self):
        """Write our owner file, exclusively; False if we lost the race.

        The owner file is created with mode "x", so it fails
        rather than overwriting an owner file another writer
        already placed in this directory. That closes the
        resume-after-delete interleaving: a writer paused
        between mkdir and the owner write, whose directory was
        broken as stale and replaced, must not write its token
        into the new owner's lock. A generic OSError from the
        create itself is different: the directory at lock_path
        may no longer be ours at all -- it may already be a
        replacement writer's lock -- and no check here can
        prove otherwise. A replacement directory is just as
        empty and fresh as our own, and any identity check
        (age, emptiness, owner file) is a separate operation
        from the delete, so the directory could still be
        swapped between the check and the delete. The only
        correct action is to delete nothing: fail cleanly and
        leave the directory for later stale cleanup. The
        conservative cost is that a genuinely own directory
        left behind strands the project until it ages past
        the unknown-owner second threshold (ten times stale),
        because it has no owner file to prove its owner dead;
        a stranded lock is never a deleted live one.
        Returns True on success, False when the file already
        exists or the directory is gone (the caller retries).
        """
        self.owner_token = "%d-%s" % (os.getpid(), uuid.uuid4().hex)
        try:
            with open(os.path.join(self.lock_path, "owner"), "x", encoding="utf-8") as handle:
                handle.write("pid=%d time=%s\n" % (os.getpid(), time.strftime("%Y-%m-%dT%H:%M:%S")))
                start = _process_start_time(os.getpid())
                handle.write("start=%s\n" % (repr(start) if start is not None else "unknown"))
                handle.write("token=%s\n" % self.owner_token)
        except FileExistsError:
            return False
        except FileNotFoundError:
            return False
        except OSError:
            # Delete nothing. The directory at lock_path may be
            # a replacement writer's lock, and nothing proves
            # it is still the directory we created, so removing
            # it could delete another writer's lock. Leave it
            # for stale cleanup and fail cleanly; the error
            # message is now literally true.
            sys.stderr.write(
                "error: could not write the lock ownership file in %s; "
                "no files were changed.\n" % self.lock_path
            )
            sys.exit(1)
        self.token_written = True
        return True

    def __exit__(self, exc_type, exc, tb):
        if self.acquired:
            # Stop the heartbeat first: from here on the lock
            # dir must go stale on its own schedule, and the
            # thread must never touch a dir we have renamed
            # aside or handed to a new owner.
            self._stop_heartbeat()
            # Rename-then-verify: the ownership decision is made on
            # the renamed copy, not on a dir a takeover can replace
            # between a check and a delete. Only a copy whose token
            # still matches ours is deleted. Anything else is put
            # back if lock_path is free, and never deleted. If
            # lock_path is occupied by a newer owner, the renamed
            # dir is left in place (see the class docstring): a
            # later acquire cleans it up once it is stale.
            trash = self._rename_lock_to_trash()
            if trash is not None:
                if (
                    self.owner_token is not None
                    and self._read_owner_token_from(trash) == self.owner_token
                ):
                    self._delete_trash(trash)
                else:
                    self._restore_trash(trash)
            self.acquired = False
        return False


CONFIG_FILENAME = "config"
CONFIG_KEYS = {
    "stale_days": int,
    "size_lines": int,
    "size_bytes": int,
    "backups_kept": int,
    "lock_timeout": float,
    "lock_stale": float,
}
DEFAULT_CONFIG = {
    "stale_days": STALE_DAYS,
    "size_lines": SIZE_LINE_LIMIT,
    "size_bytes": SIZE_BYTE_LIMIT,
    "backups_kept": BACKUPS_KEPT,
    "lock_timeout": LOCK_TIMEOUT_SECONDS,
    "lock_stale": LOCK_STALE_SECONDS,
}
# Valid ranges for `config set` (inclusive). Values outside these
# are rejected, never silently stored. stale_days 0 is allowed:
# it just flags every unstarred waiting item as stale.
CONFIG_RANGES = {
    "stale_days": (0, 1000000),
    "size_lines": (1, 1000000),
    "size_bytes": (1, 1000000000),
    "backups_kept": (0, 1000),
    "lock_timeout": (0.1, 3600),
    "lock_stale": (1, 86400),
}


def _coerce_config_value(key, raw):
    """Coerce a config value to its declared type, or raise ValueError.

    Integer keys reject fractional values instead of truncating
    them: int(1.9) is 1 in Python, so a JSON {"stale_days": 1.9}
    used to silently become 1. Integral floats (1.0) and
    integral strings ("5", "5.0") are accepted; anything
    fractional, non-finite, or non-numeric raises ValueError.
    Booleans are rejected too (int(True) is 1). Float keys
    take float(raw), so "nan"/"inf" still parse there and are
    caught downstream by config_range_error's finiteness check.
    Env overrides only cover the two float lock keys, so no
    integer truncation can happen on that path.
    """
    caster = CONFIG_KEYS[key]
    if isinstance(raw, bool):
        raise ValueError("a boolean is not a valid value for %s" % key)
    if caster is int:
        if isinstance(raw, float):
            if not math.isfinite(raw) or raw != int(raw):
                raise ValueError(
                    "%r is not an integer value for %s" % (raw, key)
                )
            return int(raw)
        if isinstance(raw, str):
            try:
                return int(raw.strip())
            except ValueError:
                number = float(raw.strip())
                if not math.isfinite(number) or number != int(number):
                    raise ValueError(
                        "%r is not an integer value for %s" % (raw, key)
                    )
                return int(number)
        return int(raw)
    return caster(raw)


def config_range_error(key, value):
    """Return an error string when value is outside its range, else None.

    Non-finite floats are rejected explicitly: NaN passes every
    plain `<` / `>` comparison, so a bare range check would accept
    it, and a NaN lock timeout would make a writer wait forever.
    Booleans are rejected too: int(True) is 1 in Python, so a JSON
    `true` must never silently become a config value.
    """
    if isinstance(value, bool):
        return "error: %s must be a number, not a boolean (got %s)\n" % (
            key,
            value,
        )
    if isinstance(value, float) and not math.isfinite(value):
        return "error: %s must be a finite number (got %s)\n" % (key, value)
    low, high = CONFIG_RANGES[key]
    if value < low or value > high:
        return "error: %s must be between %s and %s (got %s)\n" % (
            key,
            low,
            high,
            value,
        )
    return None


# A malformed .statecat/config is warned about once per process, so a
# command that loads the config several times does not spam stderr.
_config_warnings_printed = False


def config_path(directory):
    return os.path.join(directory, ".statecat", CONFIG_FILENAME)


def load_config(directory):
    """Effective config: defaults, overlaid with .statecat/config (JSON).

    Unknown keys are ignored; values that do not coerce to the right
    type, or are outside their valid range, fall back to their
    default. A missing file means plain defaults. A malformed JSON
    file warns on stderr (once per process) and falls back to
    defaults, instead of silently ignoring the breakage."""
    global _config_warnings_printed
    config = dict(DEFAULT_CONFIG)
    warnings = []
    try:
        with open(config_path(directory), "r", encoding="utf-8-sig") as handle:
            raw = json.load(handle)
    except OSError:
        return config
    except ValueError:
        warnings.append(
            "warning: .statecat/config is not valid JSON; using defaults"
        )
        raw = None
    if raw is not None:
        if not isinstance(raw, dict):
            warnings.append(
                "warning: .statecat/config is not a JSON object; using defaults"
            )
        else:
            for key, caster in CONFIG_KEYS.items():
                if key in raw:
                    if isinstance(raw[key], bool):
                        warnings.append(
                            "warning: ignoring invalid value for %s in "
                            ".statecat/config; using default %s"
                            % (key, config[key])
                        )
                        continue
                    try:
                        value = _coerce_config_value(key, raw[key])
                    except (TypeError, ValueError, OverflowError) as exc:
                        warnings.append(
                            "warning: ignoring invalid value for %s in "
                            ".statecat/config (%s); using default %s"
                            % (key, exc, config[key])
                        )
                        continue
                    range_error = config_range_error(key, value)
                    if range_error is not None:
                        warnings.append(
                            "warning: ignoring out-of-range value for %s "
                            "in .statecat/config (%s); using default %s"
                            % (key, value, config[key])
                        )
                        continue
                    config[key] = value
    # lock_stale must be greater than lock_timeout, or a waiter
    # can have its lock broken as stale while it is still
    # waiting for it. A JSON file that states an inconsistent
    # pair warns and falls back to the defaults for both lock
    # timings, rather than silently accepting the pair.
    if config["lock_stale"] <= config["lock_timeout"]:
        warnings.append(
            "warning: ignoring lock_stale/lock_timeout pair in "
            ".statecat/config (lock_stale %s is not greater than "
            "lock_timeout %s); using defaults %s / %s"
            % (
                config["lock_stale"],
                config["lock_timeout"],
                DEFAULT_CONFIG["lock_timeout"],
                DEFAULT_CONFIG["lock_stale"],
            )
        )
        config["lock_timeout"] = DEFAULT_CONFIG["lock_timeout"]
        config["lock_stale"] = DEFAULT_CONFIG["lock_stale"]
    if warnings and not _config_warnings_printed:
        for warning in warnings:
            sys.stderr.write(warning + "\n")
        _config_warnings_printed = True
    return config


def save_config(directory, config):
    ensure_dir(os.path.dirname(config_path(directory)))
    atomic_write(config_path(directory), [[json.dumps(config, indent=2), "\n"]])


def cmd_config(args):
    directory = require_root()
    if getattr(args, "action", None) == "set":
        key = args.key
        if key not in CONFIG_KEYS:
            sys.stderr.write(
                "error: unknown config key %r; known keys: %s\n"
                % (key, ", ".join(sorted(CONFIG_KEYS)))
            )
            return 1
        try:
            value = _coerce_config_value(key, args.value)
        except (TypeError, ValueError) as exc:
            sys.stderr.write(
                "error: %r is not a valid value for %s (%s)\n"
                % (args.value, key, exc)
            )
            return 1
        range_error = config_range_error(key, value)
        if range_error is not None:
            sys.stderr.write(range_error)
            return 1
        config = load_config(directory)
        # lock_stale must be greater than lock_timeout, or a
        # waiter can have its lock broken as stale while it is
        # still waiting for it. Reject the pair here, with the
        # order in which to fix it: raise lock_stale first
        # before raising lock_timeout, lower lock_timeout
        # first before lowering lock_stale.
        if key == "lock_stale" and value <= config["lock_timeout"]:
            sys.stderr.write(
                "error: lock_stale (%s) must be greater than "
                "lock_timeout (%s); lower lock_timeout first "
                "or raise lock_stale\n" % (value, config["lock_timeout"])
            )
            return 1
        if key == "lock_timeout" and value >= config["lock_stale"]:
            sys.stderr.write(
                "error: lock_timeout (%s) must be less than "
                "lock_stale (%s); raise lock_stale first or "
                "lower lock_timeout\n" % (value, config["lock_stale"])
            )
            return 1
        config[key] = value
        # Keep keys this version does not know (from a newer StateCat,
        # say) instead of silently dropping them from the file.
        try:
            with open(config_path(directory), "r", encoding="utf-8-sig") as handle:
                existing = json.load(handle)
        except (OSError, ValueError):
            existing = None
        if isinstance(existing, dict):
            existing.update(config)
            config = existing
        save_config(directory, config)
        print("set %s = %s" % (key, value))
        return 0
    print(json.dumps(load_config(directory), indent=2))
    return 0


def list_snapshots(directory):
    """Return [(name, kind, path)] sorted oldest first.

    kind is 'pre-write' or 'safety'. Safety snapshots are named
    'safety-<timestamp>' and are never the default restore target.
    """
    root = backup_root(directory)
    result = []
    if not os.path.isdir(root):
        return result
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name)
        if not os.path.isdir(path):
            continue
        if name.startswith("safety-"):
            result.append((name, "safety", path))
        else:
            result.append((name, "pre-write", path))
    return result


def prune_snapshots(directory):
    """Keep at most the configured number of snapshots in total,
    counting safety snapshots.

    The most recent pre-write snapshot is never pruned, so the
    restore default always remains available.
    """
    keep = load_config(directory)["backups_kept"]
    root = backup_root(directory)
    if not os.path.isdir(root):
        return
    snapshots = list_snapshots(directory)
    if len(snapshots) <= keep:
        return
    pre_write = [item for item in snapshots if item[1] == "pre-write"]
    latest_pre = pre_write[-1] if pre_write else None
    excess = len(snapshots) - keep
    deleted = 0
    by_time = sorted(snapshots, key=lambda item: os.path.getmtime(item[2]))
    for name, kind, path in by_time:
        if deleted >= excess:
            break
        if latest_pre is not None and (name, kind, path) == latest_pre:
            continue
        shutil.rmtree(path, ignore_errors=True)
        deleted += 1


def take_snapshot(directory, kind):
    """Copy both state files into one timestamped snapshot folder."""
    from datetime import datetime

    root = backup_root(directory)
    ensure_dir(root)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")
    name = ("safety-" + stamp) if kind == "safety" else stamp
    path = os.path.join(root, name)
    os.makedirs(path)
    for filename in (STATE, ARCHIVE):
        source = os.path.join(directory, filename)
        if os.path.exists(source):
            dest = os.path.join(path, filename)
            with open(source, "rb") as src, open(dest, "wb") as dst:
                shutil.copyfileobj(src, dst)
            # A backup of a restrictive file stays restrictive:
            # copy the source's permission bits instead of
            # leaving the umask default on the copy.
            try:
                os.chmod(dest, os.stat(source).st_mode & 0o777)
            except OSError:
                pass
    prune_snapshots(directory)
    return name


def validate_or_fail(state, archive):
    problems = structural_problems(state, archive)
    if problems:
        for problem in problems:
            sys.stderr.write("error: %s\n" % problem)
        sys.stderr.write("No files were changed. Fix the problem line or run statecat restore.\n")
        sys.exit(1)


def find_project_root(start=None):
    """Walk up from start (default cwd) until STATE.md is found."""
    directory = os.path.abspath(start or os.getcwd())
    while True:
        if os.path.exists(os.path.join(directory, STATE)):
            return directory
        parent = os.path.dirname(directory)
        if parent == directory:
            return None
        directory = parent


def find_restore_root(start=None):
    """Walk up until STATE.md or .statecat/backups is found."""
    directory = os.path.abspath(start or os.getcwd())
    while True:
        if os.path.exists(os.path.join(directory, STATE)) or os.path.isdir(
            os.path.join(directory, BACKUP_ROOT)
        ):
            return directory
        parent = os.path.dirname(directory)
        if parent == directory:
            return None
        directory = parent


def require_root():
    """Return the project root for this command, or exit cleanly."""
    root = find_project_root()
    if root is None:
        sys.stderr.write(
            "error: no %s found in this folder or any parent folder.\n" % STATE
        )
        sys.stderr.write(
            "Run statecat init in your project root; "
            "do not create a second %s in a subfolder.\n" % STATE
        )
        sys.exit(1)
    return root


def reject_newlines(text, label="item text"):
    """Reject \n / \r in item text before any snapshot or write."""
    if text is not None and ("\n" in text or "\r" in text):
        sys.stderr.write(
            "error: %s must be a single line; newlines are not allowed.\n" % label
        )
        sys.stderr.write("No files were changed and no backup was taken.\n")
        sys.exit(1)


def reject_control_chars(text, label="project name"):
    """Reject \r, \n, and other control characters in text that
    is interpolated into Markdown. A newline in a project name
    would become real file structure (headings, fake items), so
    it is refused here instead of written. Control characters
    are any code point below 0x20 or 0x7f (DEL)."""
    if text is not None and any(
        ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text
    ):
        sys.stderr.write(
            "error: %s must be a single line without control "
            "characters (no newlines, carriage returns, or "
            "other control codes).\n" % label
        )
        sys.stderr.write("No files were changed and no backup was taken.\n")
        sys.exit(1)


def template_state(project):
    return [
        "# Project: %s" % project,
        "> About: %s project state." % project,
        "> Purpose: Keep the current truth about this project in one file.",
        "> Key paths: STATE.md, STATE-ARCHIVE.md, .statecat/backups/",
        "> Last ID used: #0",
        "",
        "## Decided",
        "",
        "## Open",
        "",
        "## Waiting on human",
        "",
        "## Waiting on others",
        "",
        "## Parked",
        "",
    ]


def template_archive(project):
    return [
        "# Project: %s Archive" % project,
        "> Archive for %s. Items here are not part of the one-read load." % project,
        "> Last ID used is stored in STATE.md only.",
        "",
        "## Archived",
        "",
    ]


def lines_from_contents(contents, ending="\n"):
    return [[content, ending] for content in contents]


POINTER_LINE = (
    "Read STATE.md first, then run python3 statecat.py check "
    "(py statecat.py check on Windows if python3 is not found), "
    "before asking about or working on this project."
)
# Earlier pointer wordings, recognised so install upgrades them in
# place instead of adding a second line, and uninstall removes them.
LEGACY_POINTER_LINES = (
    "Read STATE.md first, then run python3 statecat.py check, "
    "before asking about or working on this project.",
)
POINTER_FILES = ("AGENTS.md", "CLAUDE.md")
POINTER_CREATED_HEADER = "# Project instructions\n\n"


def is_pointer_line(content):
    stripped = content.strip()
    return stripped == POINTER_LINE or stripped in LEGACY_POINTER_LINES


def install_pointer(directory, create=True):
    """Append the read-first pointer line to the project's agent
    instructions file (AGENTS.md, else CLAUDE.md), creating AGENTS.md
    when neither exists and create is true. Returns a short message
    describing what happened, or None when nothing was done."""
    target = None
    for name in POINTER_FILES:
        path = os.path.join(directory, name)
        if os.path.isfile(path):
            target = path
            break
    if target is None:
        if not create:
            return None
        target = os.path.join(directory, "AGENTS.md")
        try:
            with open(target, "w", encoding="utf-8", newline="") as handle:
                handle.write("%s%s\n" % (POINTER_CREATED_HEADER, POINTER_LINE))
        except OSError as exc:
            sys.stderr.write("warning: could not create %s: %s\n" % (target, exc))
            return None
        return "created AGENTS.md with pointer"
    try:
        # Plain utf-8, not utf-8-sig: a BOM stays in content as
        # U+FEFF, so the in-place upgrade below writes it back.
        with open(target, "r", encoding="utf-8", newline="") as handle:
            content = handle.read()
    except (OSError, UnicodeDecodeError) as exc:
        sys.stderr.write("warning: could not read %s: %s\n" % (target, exc))
        return None
    if POINTER_LINE in content:
        return "pointer already in %s" % os.path.basename(target)
    for legacy in LEGACY_POINTER_LINES:
        if legacy in content:
            try:
                atomic_write_bytes(target, content.replace(legacy, POINTER_LINE, 1).encode("utf-8"))
            except OSError as exc:
                sys.stderr.write("warning: could not update %s: %s\n" % (target, exc))
                return None
            return "updated pointer in %s" % os.path.basename(target)
    try:
        with open(target, "a", encoding="utf-8", newline="") as handle:
            if content and not content.endswith("\n"):
                handle.write("\n")
            handle.write("%s\n" % POINTER_LINE)
    except OSError as exc:
        sys.stderr.write("warning: could not update %s: %s\n" % (target, exc))
        return None
    return "added pointer to %s" % os.path.basename(target)


def ensure_gitignore(directory):
    """Make sure .statecat/ is gitignored, so paired backups are never
    committed. Creates .gitignore when there is none. Returns a short
    message, or None when nothing was done."""
    path = os.path.join(directory, ".gitignore")
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8", newline="") as handle:
                content = handle.read()
            if any(line.strip() in (".statecat/", ".statecat") for line in content.splitlines()):
                return ".statecat/ already in .gitignore"
            with open(path, "a", encoding="utf-8", newline="") as handle:
                if content and not content.endswith("\n"):
                    handle.write("\n")
                handle.write(".statecat/\n")
            return "added .statecat/ to .gitignore"
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(".statecat/\n")
        return "created .gitignore with .statecat/"
    except OSError as exc:
        sys.stderr.write("warning: could not update .gitignore: %s\n" % exc)
        return None


def cmd_init(args):
    directory = os.getcwd()
    parent_root = find_project_root(os.path.dirname(os.path.abspath(directory)))
    if parent_root is not None:
        sys.stderr.write(
            "error: %s already exists in parent project root %s; "
            "refusing to create a second state file. "
            "Use that project root instead.\n" % (STATE, parent_root)
        )
        return 1
    state_path = os.path.join(directory, STATE)
    archive_path = os.path.join(directory, ARCHIVE)
    if os.path.exists(state_path) or os.path.exists(archive_path):
        sys.stderr.write("error: %s or %s already exists; refusing to overwrite.\n" % (STATE, ARCHIVE))
        return 1
    # A blank name falls back like an omitted one, instead of writing
    # an empty "# Project:" header.
    project = (args.project or "").strip() or os.path.basename(os.path.abspath(directory)) or "My Project"
    # A newline or control character in the project name would
    # be interpolated into STATE.md as real file structure, so
    # it is refused before anything is written.
    reject_control_chars(project, "project name")
    ensure_dir(backup_root(directory))
    atomic_write(state_path, lines_from_contents(template_state(project)))
    atomic_write(archive_path, lines_from_contents(template_archive(project)))
    print("created %s" % STATE)
    print("created %s" % ARCHIVE)
    if not getattr(args, "no_pointer", False):
        message = install_pointer(directory)
        if message:
            print(message)
    if not getattr(args, "no_gitignore", False):
        message = ensure_gitignore(directory)
        if message:
            print(message)
    return 0


def uninstall_pointer(directory):
    """Remove every pointer line (current or legacy wording) from
    AGENTS.md and CLAUDE.md. A file that is left holding only the
    header init wrote when it created the file is deleted, since
    init made it. Returns a list of short messages."""
    messages = []
    for name in POINTER_FILES:
        path = os.path.join(directory, name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8", newline="") as handle:
                lines = handle.readlines()
        except (OSError, UnicodeDecodeError) as exc:
            sys.stderr.write("warning: could not read %s: %s\n" % (path, exc))
            continue
        bom = ""
        if lines and lines[0].startswith("﻿"):
            bom, lines[0] = "﻿", lines[0][1:]
        kept = [line for line in lines if not is_pointer_line(line)]
        if len(kept) == len(lines):
            continue
        remaining = "".join(kept)
        try:
            if remaining.replace("\r\n", "\n") in (POINTER_CREATED_HEADER, POINTER_CREATED_HEADER.rstrip("\n") + "\n"):
                os.unlink(path)
                messages.append("removed %s (it held only the pointer)" % name)
            else:
                atomic_write_bytes(path, (bom + remaining).encode("utf-8"))
                messages.append("removed pointer from %s" % name)
        except OSError as exc:
            sys.stderr.write("warning: could not update %s: %s\n" % (path, exc))
    return messages


def cmd_pointer(args):
    # Like the other commands, act on the project root when run from
    # a subfolder, not on a new AGENTS.md in the subfolder.
    directory = find_project_root() or os.getcwd()
    if getattr(args, "install", False):
        message = install_pointer(directory)
        if message:
            print(message)
        return 0
    if getattr(args, "uninstall", False):
        messages = uninstall_pointer(directory)
        for message in messages:
            print(message)
        if not messages:
            print("no pointer found in %s" % " or ".join(POINTER_FILES))
        return 0
    print(POINTER_LINE)
    return 0


# Claude Code SessionStart hook. It runs in a POSIX shell (Git Bash on
# Windows). It tries python3, then py, then python, because on Windows
# python3 is often a Microsoft Store stub that runs nothing, and the
# trailing exit 0 would otherwise hide that the hook did nothing.
# When statecat.py is not in the project root (installed with pip),
# it runs the installed statecat command instead.
HOOK_COMMAND = (
    'cd "$CLAUDE_PROJECT_DIR" && { if [ -f statecat.py ]; then '
    "P=python3; $P -c '' 2>/dev/null || P=py; $P -c '' 2>/dev/null || P=python; "
    'S="$P statecat.py"; else S=statecat; fi; '
    "$S show && $S check; }; exit 0"
)
HOOK_SETTINGS = os.path.join(".claude", "settings.json")


def hook_entry():
    return {"hooks": [{"type": "command", "command": HOOK_COMMAND}]}


def is_statecat_hook(hook):
    command = hook.get("command") if isinstance(hook, dict) else None
    if not isinstance(command, str):
        return False
    if command == HOOK_COMMAND:
        return True
    # Older StateCat hook commands, recognised so install upgrades them.
    return "statecat.py show" in command and "statecat.py check" in command


def load_hook_settings(path):
    """Return (settings, error). A missing file gives {}. Anything
    that is not a JSON object with an object 'hooks' and a list
    'SessionStart' is an error, so a file we cannot safely merge
    into is never overwritten."""
    if not os.path.exists(path):
        return {}, None
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            settings = json.load(handle)
    except OSError as exc:
        return None, "could not read %s (%s); nothing was changed" % (path, exc)
    except ValueError as exc:
        return None, "%s is not valid JSON (%s); fix it by hand, nothing was changed" % (path, exc)
    if not isinstance(settings, dict):
        return None, "%s is not a JSON object; nothing was changed" % path
    hooks = settings.get("hooks", {})
    if not isinstance(hooks, dict):
        return None, "'hooks' in %s is not an object; nothing was changed" % path
    if not isinstance(hooks.get("SessionStart", []), list):
        return None, "'hooks.SessionStart' in %s is not a list; nothing was changed" % path
    return settings, None


def write_hook_settings(path, settings):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_bytes(path, (json.dumps(settings, indent=2) + "\n").encode("utf-8"))


def cmd_hook(args):
    if not (args.install or args.uninstall):
        print(json.dumps({"hooks": {"SessionStart": [hook_entry()]}}, indent=2))
        return 0
    directory = find_project_root() or os.getcwd()
    path = os.path.join(directory, HOOK_SETTINGS)
    settings, error = load_hook_settings(path)
    if error:
        sys.stderr.write("error: %s\n" % error)
        return 1
    groups = settings.get("hooks", {}).get("SessionStart", [])
    found = [
        (group, hook)
        for group in groups if isinstance(group, dict) and isinstance(group.get("hooks"), list)
        for hook in group["hooks"] if is_statecat_hook(hook)
    ]
    if args.install:
        if any(hook.get("command") == HOOK_COMMAND for _group, hook in found):
            print("SessionStart hook already in %s" % HOOK_SETTINGS)
            return 0
        if found:
            # An older StateCat command: upgrade the first, drop the rest.
            found[0][1]["command"] = HOOK_COMMAND
            for group, hook in found[1:]:
                group["hooks"].remove(hook)
            message = "updated SessionStart hook in %s" % HOOK_SETTINGS
        else:
            settings.setdefault("hooks", {}).setdefault("SessionStart", []).append(hook_entry())
            message = "added SessionStart hook to %s" % HOOK_SETTINGS
    else:
        if not found:
            print("no StateCat SessionStart hook in %s" % HOOK_SETTINGS)
            return 0
        emptied = []
        for group, hook in found:
            group["hooks"].remove(hook)
            if not group["hooks"]:
                emptied.append(group)
        hooks = settings["hooks"]
        # Drop only groups this uninstall emptied, never ones the user left empty.
        hooks["SessionStart"] = [
            group for group in hooks["SessionStart"]
            if not any(group is empty for empty in emptied)
        ]
        if not hooks["SessionStart"]:
            del hooks["SessionStart"]
        if not hooks:
            del settings["hooks"]
        message = "removed SessionStart hook from %s" % HOOK_SETTINGS
    try:
        write_hook_settings(path, settings)
    except OSError as exc:
        sys.stderr.write("error: could not write %s: %s\n" % (path, exc))
        return 1
    print(message)
    return 0


def project_name(state):
    """Project name from the '# Project: X' header line, or None."""
    for content, _ending in state.lines:
        if content.startswith("# Project:"):
            return content.split(":", 1)[1].strip() or None
    return None


def item_to_dict(item, parsed=None):
    data = {
        "id": item.item_id,
        "date": item.item_date,
        "starred": item.starred,
        "text": item.text,
    }
    if parsed is not None:
        # Hand-wrapped lines, stripped, so JSON readers see the whole item.
        data["continuation"] = [parsed.lines[index][0].strip() for index in item.continuation_indices]
    return data


def cmd_show(args):
    directory = require_root()
    state, _archive = read_state_and_archive(directory)
    if getattr(args, "json", False):
        buckets = [args.bucket] if args.bucket else list(BUCKETS)
        if args.bucket:
            bucket = BUCKET_ALIASES.get(args.bucket.strip().lower())
            if bucket is None:
                sys.stderr.write(unknown_bucket_message(args.bucket))
                return 1
            buckets = [bucket]
        data = {
            "project": project_name(state),
            "last_id": state.last_id,
            "buckets": {
                bucket: [item_to_dict(item, state) for item in state.items if item.bucket == bucket]
                for bucket in buckets
            },
        }
        print(json.dumps(data, indent=2))
        return 0
    if args.bucket:
        bucket = BUCKET_ALIASES.get(args.bucket.strip().lower())
        if bucket is None:
            sys.stderr.write(unknown_bucket_message(args.bucket))
            return 1
        start, end = state.bucket_range(bucket)
        if start is None:
            sys.stderr.write("error: bucket %r not found in %s\n" % (bucket, STATE))
            return 1
        for content, ending in state.lines[start:end]:
            sys.stdout.write(content + ending)
    else:
        for content, ending in state.lines:
            sys.stdout.write(content + ending)
    return 0


def cmd_status(args):
    directory = require_root()
    state, archive = read_state_and_archive(directory)
    problems = structural_problems(state, archive)
    warnings = warnings_for(state, archive, directory)
    stale = [warning for warning in warnings if warning.startswith("stale:")]
    counts = {bucket: 0 for bucket in BUCKETS}
    for item in state.items:
        if item.bucket in counts:
            counts[item.bucket] += 1
    print("StateCat status: %s" % (project_name(state) or "Unnamed project"))
    print(", ".join("%s: %d" % (bucket, counts[bucket]) for bucket in BUCKETS))
    print("Archived: %d" % len(archive.items))
    if stale:
        print("Stale waiting items: %d" % len(stale))
    print("Last ID used: #%s" % (state.last_id if state.last_id is not None else "?"))
    try:
        size = os.path.getsize(os.path.join(directory, STATE))
    except OSError:
        size = 0
    print("%s: %d lines, %d bytes" % (STATE, len(state.lines), size))
    if problems:
        print("check: %d structural problem(s)" % len(problems))
        return 1
    if warnings:
        print("check: %d warning(s)" % len(warnings))
    else:
        print("check: clean")
    return 0


def item_full_text(parsed, item):
    """An item's text with its hand-wrapped continuation lines joined
    on, so search finds words that sit on a wrapped line."""
    parts = [item.text] + [parsed.lines[index][0].strip() for index in item.continuation_indices]
    return " ".join(part for part in parts if part)


def cmd_search(args):
    directory = require_root()
    state, archive = read_state_and_archive(directory)
    needle = args.text.casefold()
    found = []
    for item in state.items:
        if needle in item_full_text(state, item).casefold():
            found.append((item.bucket, item, item_full_text(state, item)))
    if getattr(args, "archive", False):
        for item in archive.items:
            if needle in item_full_text(archive, item).casefold():
                found.append(("Archived", item, item_full_text(archive, item)))
    if not found:
        print("no matches for %r" % args.text)
        return 1
    for bucket, item, text in found:
        item_id = "#%d" % item.item_id if item.item_id is not None else "#?"
        item_date = item.item_date or "no date"
        if item.starred:
            item_date += "*"
        print("- %s [%s] (%s) %s" % (item_id, item_date, bucket, text))
    return 0


def item_block_lines(state, item):
    """Return the raw lines for an item plus its continuations."""
    end = item.line_index + 1 + len(item.continuation_indices)
    return [list(pair) for pair in state.lines[item.line_index:end]]


def terminate_line_before(state, index, ending):
    """Give the line before index a line ending if it has none (the
    last line of a file saved without a final newline), so an
    inserted line cannot be glued onto it."""
    if index > 0 and not state.lines[index - 1][1]:
        state.lines[index - 1][1] = ending


def insert_block_before_heading(state, index, block_lines):
    """Insert a whole item block, leaving a blank line before a heading."""
    ending = state.dominant_ending()
    terminate_line_before(state, index, ending)
    for offset, pair in enumerate(block_lines):
        # A block taken from the end of a file may have no newline.
        state.lines.insert(index + offset, [pair[0], pair[1] or ending])
    after = index + len(block_lines)
    if after < len(state.lines) and state.lines[after][0].startswith("## "):
        if state.lines[after - 1][0] != "":
            state.lines.insert(after, ["", ending])
    return index


def insert_line_before_heading(state, index, line_content):
    """Insert a line at index, leaving a blank line before a heading.

    Only the inserted lines are new; untouched lines are not
    reformatted, re-wrapped, or reordered.
    """
    ending = state.dominant_ending()
    terminate_line_before(state, index, ending)
    state.lines.insert(index, [line_content, ending])
    if index + 1 < len(state.lines) and state.lines[index + 1][0].startswith("## "):
        if state.lines[index][0] != "":
            state.lines.insert(index + 1, ["", ending])
    return index


def bucket_insert_index(state, start, end):
    """Where a new item goes in the section [start, end): right after
    its last non-blank line, so it joins the item list instead of
    landing after the blank line that separates the section from the
    next heading. In an empty section it goes after the heading's
    blank line, if there is one."""
    index = end
    while index - 1 > start and state.lines[index - 1][0].strip() == "":
        index -= 1
    if index == start + 1 and index < end and state.lines[index][0].strip() == "":
        index += 1
    return index


def delete_item_block(state, item):
    """Delete an item and its continuations. When that leaves two
    blank lines next to each other where the item was, drop one, so
    repeated add/remove cycles do not pile up blank lines. No other
    line is touched."""
    index = item.line_index
    del state.lines[index:index + 1 + len(item.continuation_indices)]
    if (
        0 < index < len(state.lines)
        and state.lines[index - 1][0].strip() == ""
        and state.lines[index][0].strip() == ""
    ):
        del state.lines[index]


def insert_into_bucket(state, bucket, line_content):
    """Insert a line at the end of a bucket's item list."""
    start, end = state.bucket_range(bucket)
    if start is None:
        fail_cleanly("bucket %r not found in %s" % (bucket, STATE))
    index = bucket_insert_index(state, start, end)
    insert_line_before_heading(state, index, line_content)
    return index


def cmd_add(args):
    directory = require_root()
    state, archive = read_state_and_archive(directory)
    validate_or_fail(state, archive)
    bucket = BUCKET_ALIASES.get(args.bucket.strip().lower())
    if bucket is None:
        sys.stderr.write(unknown_bucket_message(args.bucket))
        return 1
    if args.text is None or not args.text.strip():
        sys.stderr.write("error: item text must not be empty\n")
        return 1
    reject_newlines(args.text, "item text")
    if args.because:
        reject_newlines(args.because, "because text")
    text = args.text
    if args.because:
        text = "%s, because %s" % (text, args.because)
    new_id = state.next_id(archive)
    take_snapshot(directory, "pre-write")
    state.set_last_id(new_id)
    line = "- #%d [%s] %s" % (new_id, today_str(), text)
    insert_into_bucket(state, bucket, line)
    atomic_write(os.path.join(directory, STATE), state.lines)
    print("added #%d" % new_id)
    return 0


def find_item_or_fail(state, item_id):
    item = state.find(item_id)
    if item is None:
        sys.stderr.write(
            "error: no item #%d in %s; run '%s statecat.py show' "
            "to list items\n" % (item_id, STATE, "py" if sys.platform == "win32" else "python3")
        )
        sys.exit(1)
    return item


def _move_impl(directory, state, archive, item, bucket):
    """Move an item object already located in `state` to `bucket`.

    The item's indented continuations move with it, as one block.
    """
    old_index = item.line_index
    ending = item.ending
    block = item_block_lines(state, item)
    new_content = item.render()
    # Update the date on the move.
    new_item = Item(old_index, new_content, ending, bucket)
    new_item.item_date = today_str()
    new_item.starred = False
    rendered = new_item.render()
    block[0] = [rendered, block[0][1]]
    delete_item_block(state, item)
    # Find the target bucket in the shortened file.
    start, end = state.bucket_range(bucket)
    if start is None:
        fail_cleanly("bucket %r not found in %s" % (bucket, STATE))
    insert_block_before_heading(state, bucket_insert_index(state, start, end), block)
    atomic_write(os.path.join(directory, STATE), state.lines)


def cmd_move_real(args):
    directory = require_root()
    state, archive = read_state_and_archive(directory)
    validate_or_fail(state, archive)
    item_id = parse_id(args.item)
    if item_id is None:
        sys.stderr.write("error: bad item id %r\n" % args.item)
        return 1
    item = find_item_or_fail(state, item_id)
    bucket = BUCKET_ALIASES.get(args.bucket.strip().lower())
    if bucket is None:
        sys.stderr.write(unknown_bucket_message(args.bucket))
        return 1
    if item.bucket == "Parked" and not args.owner_approved:
        sys.stderr.write("error: #%d is Parked; moving out of Parked requires --owner-approved.\n" % item_id)
        return 1
    if item.bucket == bucket:
        print("#%d is already in %s; nothing changed" % (item_id, bucket))
        return 0
    take_snapshot(directory, "pre-write")
    state, archive = read_state_and_archive(directory)
    item = find_item_or_fail(state, item_id)
    _move_impl(directory, state, archive, item, bucket)
    print("moved #%d to %s" % (item_id, bucket))
    return 0


def cmd_update(args):
    directory = require_root()
    state, archive = read_state_and_archive(directory)
    validate_or_fail(state, archive)
    item_id = parse_id(args.item)
    if item_id is None:
        sys.stderr.write("error: bad item id %r\n" % args.item)
        return 1
    item = find_item_or_fail(state, item_id)
    if item.bucket == "Parked" and not args.owner_approved:
        sys.stderr.write("error: #%d is Parked; updating a parked item requires --owner-approved.\n" % item_id)
        return 1
    if args.text is None or not args.text.strip():
        sys.stderr.write("error: item text must not be empty\n")
        return 1
    reject_newlines(args.text, "item text")
    take_snapshot(directory, "pre-write")
    state, archive = read_state_and_archive(directory)
    item = find_item_or_fail(state, item_id)
    item.text = args.text
    item.item_date = today_str()
    item.starred = False
    state.lines[item.line_index][0] = item.render()
    atomic_write(os.path.join(directory, STATE), state.lines)
    print("updated #%d" % item_id)
    return 0


def append_to_archive(archive, line_content, ending):
    """Append a line to the Archived section of the archive file."""
    start, end = archive.bucket_range("Archived")
    if start is None:
        fail_cleanly(
            "archive file has no '## Archived' heading; "
            "refusing to create a headerless archive entry"
        )
    insert_line_before_heading(archive, bucket_insert_index(archive, start, end), line_content)


def append_block_to_archive(archive, block_lines):
    """Append a whole item block to the Archived section."""
    start, end = archive.bucket_range("Archived")
    if start is None:
        fail_cleanly(
            "archive file has no '## Archived' heading; "
            "refusing to create a headerless archive entry"
        )
    insert_block_before_heading(archive, bucket_insert_index(archive, start, end), block_lines)


def cmd_supersede(args):
    directory = require_root()
    state, archive = read_state_and_archive(directory)
    validate_or_fail(state, archive)
    item_id = parse_id(args.item)
    if item_id is None:
        sys.stderr.write("error: bad item id %r\n" % args.item)
        return 1
    item = find_item_or_fail(state, item_id)
    if item.bucket != "Decided":
        sys.stderr.write("error: supersede only applies to Decided items; #%d is in %s\n" % (item_id, item.bucket))
        return 1
    if args.text is None or not args.text.strip():
        sys.stderr.write("error: item text must not be empty\n")
        return 1
    reject_newlines(args.text, "item text")
    if args.because:
        reject_newlines(args.because, "because text")
    new_id = state.next_id(archive)
    old_content = item.content
    old_ending = item.ending
    new_text = "%s (supersedes #%d)" % (args.text, item_id)
    if args.because:
        new_text = "%s, because %s" % (new_text, args.because)
    take_snapshot(directory, "pre-write")
    # Archive first, then STATE.md, each atomically.
    state, archive = read_state_and_archive(directory)
    item = find_item_or_fail(state, item_id)
    block = item_block_lines(state, item)
    append_block_to_archive(archive, block)
    atomic_write(os.path.join(directory, ARCHIVE), archive.lines)
    state, archive = read_state_and_archive(directory)
    item = find_item_or_fail(state, item_id)
    state.set_last_id(new_id)
    delete_item_block(state, item)
    new_line = "- #%d [%s] %s" % (new_id, today_str(), new_text)
    insert_into_bucket(state, "Decided", new_line)
    atomic_write(os.path.join(directory, STATE), state.lines)
    print("superseded #%d with #%d" % (item_id, new_id))
    return 0


def compact_candidates(state, days):
    """Decided items older than `days`, and how many Decided items
    were skipped because their age is unknown (no date, starred
    date, or unparseable date). Only Decided items are ever compacted:
    Open and waiting items are still live."""
    today = date.today()
    candidates = []
    skipped = 0
    for item in state.items:
        if item.bucket != "Decided":
            continue
        age = item_age_days(item, today)
        if age is None:
            skipped += 1
        elif age > days:
            candidates.append(item)
    return candidates, skipped


def cmd_compact(args):
    if args.older_than < 0:
        sys.stderr.write("error: --older-than must be 0 or more days (got %d)\n" % args.older_than)
        return 1
    directory = require_root()
    state, archive = read_state_and_archive(directory)
    problems = structural_problems(state, archive)
    if problems:
        sys.stderr.write("error: structural problems must be fixed before compact can run.\n")
        return 1
    candidates, skipped = compact_candidates(state, args.older_than)
    if not candidates:
        print("nothing to compact")
        if skipped:
            print("skipped %d Decided item(s) with unknown age" % skipped)
        return 0
    if args.dry_run:
        for item in candidates:
            print("would archive #%d %s" % (item.item_id, item.text))
        return 0
    take_snapshot(directory, "pre-write")
    state, archive = read_state_and_archive(directory)
    candidates, _skipped = compact_candidates(state, args.older_than)
    # Archive in file order, so the archive keeps the items' order,
    # then delete in reverse line order so earlier indices stay valid.
    in_order = sorted(candidates, key=lambda candidate: candidate.line_index)
    for item in in_order:
        append_block_to_archive(archive, item_block_lines(state, item))
    for item in reversed(in_order):
        delete_item_block(state, item)
    atomic_write(os.path.join(directory, ARCHIVE), archive.lines)
    atomic_write(os.path.join(directory, STATE), state.lines)
    for item in sorted(candidates, key=lambda candidate: candidate.item_id):
        print("archived #%d" % item.item_id)
    return 0


def cmd_archive(args):
    directory = require_root()
    state, archive = read_state_and_archive(directory)
    validate_or_fail(state, archive)
    item_id = parse_id(args.item)
    if item_id is None:
        sys.stderr.write("error: bad item id %r\n" % args.item)
        return 1
    item = find_item_or_fail(state, item_id)
    if item.bucket == "Parked" and not args.owner_approved:
        sys.stderr.write("error: #%d is Parked; archiving a parked item requires --owner-approved.\n" % item_id)
        return 1
    take_snapshot(directory, "pre-write")
    state, archive = read_state_and_archive(directory)
    item = find_item_or_fail(state, item_id)
    block = item_block_lines(state, item)
    append_block_to_archive(archive, block)
    atomic_write(os.path.join(directory, ARCHIVE), archive.lines)
    state, _archive = read_state_and_archive(directory)
    item = find_item_or_fail(state, item_id)
    delete_item_block(state, item)
    atomic_write(os.path.join(directory, STATE), state.lines)
    print("archived #%d" % item_id)
    return 0


def cmd_check(args):
    directory = require_root()
    state, archive = read_state_and_archive(directory)
    problems = structural_problems(state, archive)
    warnings = warnings_for(state, archive, directory)
    for problem in problems:
        print("problem: %s" % problem)
    for warning in warnings:
        print(warning)
    if getattr(args, "fix", False) or getattr(args, "fix_ids", False):
        if problems:
            sys.stderr.write("error: structural problems must be fixed before --fix can run.\n")
            return 1
        # --fix is exempt from the Parked guard: it only adds IDs/dates.
        # Fix in memory first and snapshot only when there is something
        # to write: a no-op --fix used to add an identical snapshot each
        # run, pushing real ones out of backups_kept and making the
        # no-op copy the default restore target. The lock is held, so
        # the files on disk are still the ones read here.
        fixed = []
        today = today_str()
        for item in list(state.items):
            changed = False
            if item.item_id is None:
                new_id = state.next_id(archive)
                item.item_id = new_id
                state.set_last_id(new_id)
                changed = True
            if item.item_date is None:
                item.item_date = today
                item.starred = True
                changed = True
            if changed:
                state.lines[item.line_index][0] = item.render()
                fixed.append(item.item_id)
        # Raise a drifted counter to the highest ID actually in use.
        highest = 0
        for parsed in (state, archive):
            for item_id in parsed.all_ids():
                if item_id > highest:
                    highest = item_id
        counter_fixed = False
        if state.last_id is None:
            # The Last ID used line itself is missing: restore it
            # from the highest ID in use (or #0 when there are none),
            # instead of reporting "nothing to fix".
            state.set_last_id(highest)
            counter_fixed = True
        elif highest > state.last_id:
            state.set_last_id(highest)
            counter_fixed = True
        if fixed or counter_fixed:
            take_snapshot(directory, "pre-write")
            atomic_write(os.path.join(directory, STATE), state.lines)
            for fixed_id in fixed:
                print("fixed #%d" % fixed_id)
            if counter_fixed:
                print("fixed Last ID used to #%d" % highest)
        else:
            print("check: nothing to fix")
        return 0
    if problems:
        print("check: %d structural problem(s)" % len(problems))
        return 1
    if warnings:
        print("check: %d warning(s)" % len(warnings))
        return 0
    print("check: clean")
    return 0


def find_snapshot(snapshots, name):
    """Find one snapshot by exact name or unique prefix.

    Returns (name, kind, path), or None when there is no match.
    Raises ValueError on an ambiguous prefix."""
    match = None
    for candidate in snapshots:
        if candidate[0] == name or candidate[0].startswith(name):
            if match is not None:
                raise ValueError("ambiguous snapshot %r" % name)
            match = candidate
    return match


def snapshot_is_complete(target_path, target_name):
    """Validate a snapshot BEFORE anything is overwritten.

    Both files must be present and parse as UTF-8. A snapshot that
    fails here is refused with a clean error and nothing is
    restored, instead of silently skipping the missing file while
    printing success. Structural problems in a snapshot do not
    fail validation: restoring a broken snapshot still warns after
    the restore, as before.
    """
    for filename in (STATE, ARCHIVE):
        path = os.path.join(target_path, filename)
        if not os.path.exists(path):
            sys.stderr.write(
                "error: snapshot %r is incomplete: %s is missing; "
                "nothing was restored.\n" % (target_name, filename)
            )
            return False
    # Parsing both files also rejects invalid UTF-8, via StateFile,
    # before any file is touched.
    StateFile(os.path.join(target_path, STATE))
    StateFile(os.path.join(target_path, ARCHIVE))
    return True


def atomic_write_pair(directory, data_by_filename):
    """Write both state files as a pair: stage both temp files
    first, then rename both back-to-back. That cannot make two
    renames truly atomic, but no file I/O happens between them,
    so the window in which a crash could leave a mixed pair is
    just the two renames. Each file keeps its own target's
    permission bits (umask for a new file), like atomic_write."""
    staged = []
    try:
        for filename, data in data_by_filename:
            target = _write_target(os.path.join(directory, filename))
            tmp_dir = os.path.dirname(target) or "."
            handle, tmp_path = tempfile.mkstemp(dir=tmp_dir, prefix=".statecat-tmp-")
            with os.fdopen(handle, "wb") as out:
                out.write(data)
            os.chmod(tmp_path, _mode_for_write(target))
            staged.append((tmp_path, target))
        for tmp_path, target in staged:
            os.replace(tmp_path, target)
    except BaseException:
        for tmp_path, _target in staged:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        raise


def _available_snapshots_text(snapshots):
    if not snapshots:
        return "no snapshots are available"
    return "available snapshots: %s" % ", ".join(
        name for name, _kind, _path in snapshots
    )


def cmd_restore(args):
    directory = find_restore_root()
    if directory is None:
        if args.list:
            print("no snapshots")
            return 0
        sys.stderr.write(
            "error: no %s or .statecat/backups found in this folder or any "
            "parent folder; no snapshots to restore.\n" % STATE
        )
        return 1
    snapshots = list_snapshots(directory)
    if args.list:
        if not snapshots:
            print("no snapshots")
            return 0
        for name, kind, _path in snapshots:
            print("%s %s" % (name, kind))
        return 0
    if args.snapshot:
        try:
            match = find_snapshot(snapshots, args.snapshot)
        except ValueError as exc:
            sys.stderr.write("error: %s\n" % exc)
            return 1
        if match is None:
            sys.stderr.write("error: no snapshot %r\n" % args.snapshot)
            sys.stderr.write("%s\n" % _available_snapshots_text(snapshots))
            return 1
        target_name, _kind, target_path = match
    else:
        pre_write = [item for item in snapshots if item[1] == "pre-write"]
        if not pre_write:
            sys.stderr.write("error: no pre-write snapshot to restore\n")
            return 1
        target_name, _kind, target_path = pre_write[-1]
    if not snapshot_is_complete(target_path, target_name):
        return 1
    # Safety snapshot first; it is never the default target.
    take_snapshot(directory, "safety")
    data_by_filename = []
    for filename in (STATE, ARCHIVE):
        with open(os.path.join(target_path, filename), "rb") as handle:
            data_by_filename.append((filename, handle.read()))
    atomic_write_pair(directory, data_by_filename)
    print("restored %s" % target_name)
    # Verify what was restored: a snapshot can itself carry structural
    # problems, and installing it silently would trade one broken
    # state for another.
    state, archive = read_state_and_archive(directory)
    problems = structural_problems(state, archive)
    if problems:
        print("warning: restored files have structural problems:")
        for problem in problems:
            print("problem: %s" % problem)
    return 0


def cmd_diff(args):
    directory = require_root()
    snapshots = list_snapshots(directory)
    if args.snapshot:
        try:
            match = find_snapshot(snapshots, args.snapshot)
        except ValueError as exc:
            sys.stderr.write("error: %s\n" % exc)
            return 1
        if match is None:
            sys.stderr.write("error: no snapshot %r\n" % args.snapshot)
            sys.stderr.write("%s\n" % _available_snapshots_text(snapshots))
            return 1
        target_name, _kind, target_path = match
    else:
        pre_write = [item for item in snapshots if item[1] == "pre-write"]
        if not pre_write:
            sys.stderr.write("error: no pre-write snapshot to diff against\n")
            return 1
        target_name, _kind, target_path = pre_write[-1]
    state, archive = read_state_and_archive(directory)
    changed = False
    for filename, current in ((STATE, state.lines), (ARCHIVE, archive.lines)):
        snapshot_file = os.path.join(target_path, filename)
        if not os.path.exists(snapshot_file):
            continue
        try:
            # utf-8-sig, like StateFile reads the current files, so a
            # BOM alone never shows up as a changed first line.
            with open(snapshot_file, "r", encoding="utf-8-sig", newline="") as handle:
                old = handle.readlines()
        except UnicodeDecodeError:
            fail_cleanly(
                "snapshot %s file %s is not valid UTF-8" % (target_name, filename)
            )
        new = [content + ending for content, ending in current]
        diff = list(difflib.unified_diff(
            old, new,
            fromfile="%s/%s" % (target_name, filename),
            tofile=filename,
        ))
        if diff:
            changed = True
            for line in diff:
                sys.stdout.write(line if line.endswith("\n") else line + "\n")
    if not changed:
        print("no differences")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="statecat",
        description=(
            "StateCat v1.3.0: one living project state, readable by any agent "
            "in one read. Local only, plain Markdown, no network calls."
        ),
    )
    parser.add_argument("--version", action="version", version="statecat " + VERSION)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="Create STATE.md, STATE-ARCHIVE.md, and .statecat/backups/")
    p.add_argument("project", nargs="?", help="Project name for the About header")
    p.add_argument("--no-pointer", action="store_true", help="Do not install the read-first pointer line")
    p.add_argument("--no-gitignore", action="store_true", help="Do not add .statecat/ to .gitignore")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("pointer", help="Print the read-first pointer line, or install/uninstall it")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--install", action="store_true", help="Append the pointer line to AGENTS.md (or CLAUDE.md)")
    group.add_argument("--uninstall", action="store_true", help="Remove the pointer line from AGENTS.md and CLAUDE.md")
    p.set_defaults(func=cmd_pointer)

    p = sub.add_parser("hook", help="Print the Claude Code SessionStart hook, or install/uninstall it")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--install", action="store_true", help="Add the hook to .claude/settings.json, keeping other settings")
    group.add_argument("--uninstall", action="store_true", help="Remove the StateCat hook from .claude/settings.json")
    p.set_defaults(func=cmd_hook)

    p = sub.add_parser("show", help="Print the full state, or one bucket")
    p.add_argument("bucket", nargs="?", help="Bucket name, e.g. Open")
    p.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of Markdown")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("status", help="Print a compact summary: counts, stale items, size, check result")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("search", help="Search item text (case-insensitive substring); exits 1 when nothing matches",
                         description="Search item text (case-insensitive substring). Exits 1 when nothing matches.")
    p.add_argument("text", help="Text to find")
    p.add_argument("--archive", action="store_true", help="Also search STATE-ARCHIVE.md")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("add", help="Add a dated line to a bucket")
    p.add_argument("bucket", help="Bucket name")
    p.add_argument("text", help="Item text")
    p.add_argument("--because", help="Rationale, appended as ', because ...'")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("move", help="Move an item to another bucket by ID")
    p.add_argument("item", help="Item ID, e.g. 12")
    p.add_argument("bucket", help="Target bucket name")
    p.add_argument("--owner-approved", action="store_true", help="Required to move out of Parked")
    p.set_defaults(func=cmd_move_real)

    p = sub.add_parser("update", help="Replace an item's wording by ID")
    p.add_argument("item", help="Item ID, e.g. 12")
    p.add_argument("text", help="New wording")
    p.add_argument("--owner-approved", action="store_true", help="Required for a parked item")
    p.set_defaults(func=cmd_update)

    p = sub.add_parser("supersede", help="Replace a Decided item with a new ID, archiving the old one")
    p.add_argument("item", help="Decided item ID, e.g. 3")
    p.add_argument("text", help="Replacement wording")
    p.add_argument("--because", help="Rationale, appended as ', because ...'")
    p.set_defaults(func=cmd_supersede)

    p = sub.add_parser("archive", help="Move an item to STATE-ARCHIVE.md by ID")
    p.add_argument("item", help="Item ID, e.g. 12")
    p.add_argument("--owner-approved", action="store_true", help="Required for a parked item")
    p.set_defaults(func=cmd_archive)

    p = sub.add_parser("compact", help="Archive Decided items older than N days")
    p.add_argument("--older-than", type=int, default=90, metavar="DAYS", help="Archive Decided items older than this (default 90)")
    p.add_argument("--dry-run", action="store_true", help="List what would be archived, without writing")
    p.set_defaults(func=cmd_compact)

    p = sub.add_parser("config", help="Show the effective .statecat/config, or set one key")
    p.add_argument("action", nargs="?", choices=["set"], help="Use 'set' to change one key")
    p.add_argument("key", nargs="?", help="Config key, e.g. stale_days")
    p.add_argument("value", nargs="?", help="New value for the key")
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("check", help="Report structural problems and warnings")
    p.add_argument("--fix", action="store_true", help="Assign missing IDs and starred dates (Parked-guard exempt)")
    p.add_argument("--fix-ids", dest="fix_ids", action="store_true", help="Alias for --fix")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("restore", help="Restore a paired snapshot of both files")
    p.add_argument("snapshot", nargs="?", help="Snapshot name, or omit for the most recent pre-write snapshot")
    p.add_argument("--list", action="store_true", help="List snapshots")
    p.set_defaults(func=cmd_restore)

    p = sub.add_parser("diff", help="Show the unified diff between a snapshot and the current files")
    p.add_argument("snapshot", nargs="?", help="Snapshot name, or omit for the most recent pre-write snapshot")
    p.set_defaults(func=cmd_diff)

    return parser


def use_utf8_output():
    """State files are UTF-8, so output is too. On Windows a piped
    stdout (an agent, a hook, `| cat`) defaults to the ANSI code
    page, and printing any item with a character outside it, such
    as a check mark, crashed with UnicodeEncodeError."""
    for stream in (sys.stdout, sys.stderr):
        encoding = (getattr(stream, "encoding", None) or "").lower().replace("-", "").replace("_", "")
        if encoding != "utf8":
            try:
                stream.reconfigure(encoding="utf-8")
            except (AttributeError, ValueError, OSError):
                pass


def main(argv=None):
    use_utf8_output()
    parser = build_parser()
    args = parser.parse_args(argv)
    # Write commands run whole read-modify-write cycles under the
    # project lock, so two writers cannot silently lose an item.
    # Read-only commands that report on both state files (show,
    # status, search, check, diff) take the lock too: a
    # supersede / archive / compact writes STATE-ARCHIVE.md
    # first and STATE.md second, and a reader in between would
    # otherwise see the same ID in both files. The lock is
    # exclusive, so a reader may wait briefly for a writer to
    # finish (up to the lock timeout); that is the price of
    # never reporting an inconsistent pair. Commands that read
    # neither state file (pointer, config show, restore --list)
    # stay unlocked.
    # init and config set are writers too, and used to bypass the
    # lock: two agents init-ing the same empty directory could
    # interleave and leave STATE.md and STATE-ARCHIVE.md with
    # different project headers, and two simultaneous config
    # sets could each read the same old JSON and lose the other.
    # init has no STATE.md yet, so it cannot use require_root:
    # its lock lives in .statecat/ under the current directory,
    # which the lock creates itself, and the lock does not
    # depend on the state files existing. The second init still
    # fails cleanly inside the lock, once the first has created
    # the files, instead of overwriting them.
    if args.command in (
        "add", "move", "update", "supersede", "archive", "compact",
        "show", "status", "search", "check", "diff",
    ):
        directory = require_root()
        with project_lock(directory):
            return args.func(args)
    if args.command == "init":
        with project_lock(os.getcwd()):
            return args.func(args)
    if args.command == "config" and getattr(args, "action", None) == "set":
        directory = require_root()
        with project_lock(directory):
            return args.func(args)
    if args.command == "restore" and not args.list:
        directory = find_restore_root()
        if directory is not None:
            with project_lock(directory):
                return args.func(args)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
