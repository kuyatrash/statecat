#!/usr/bin/env python3
"""StateCat v1: one living project state, readable by any agent in one read.

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
import os
import re
import shutil
import sys
import tempfile
from datetime import date

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


def today_str():
    return date.today().isoformat()


def parse_id(text):
    """Accept '12' or '#12', return int or None."""
    if text is None:
        return None
    text = text.strip()
    if text.startswith("#"):
        text = text[1:]
    if not text.isdigit():
        return None
    return int(text)


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
        self.starred = False
        self.text = ""
        self.continuation_indices = []
        self.parse()

    def parse(self):
        body = self.content
        if not body.startswith("- "):
            return
        rest = body[2:]
        m = re.match(r"#(\d+) \[(\d{4}-\d{2}-\d{2})(\*)?\] (.*)$", rest)
        if m:
            self.item_id = int(m.group(1))
            self.item_date = m.group(2)
            self.starred = bool(m.group(3))
            self.text = m.group(4)
            return
        m = re.match(r"#(\d+) (.*)$", rest)
        if m:
            self.item_id = int(m.group(1))
            self.text = m.group(2)
            return
        m = re.match(r"\[(\d{4}-\d{2}-\d{2})(\*)?\] (.*)$", rest)
        if m:
            self.item_date = m.group(1)
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
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8", newline="") as handle:
                raw = handle.readlines()
            for content, ending in (split_ending(line) for line in raw):
                self.lines.append([content, ending])
        self._parse()

    def _parse(self):
        bucket = None
        last_item = None
        for index, (content, _ending) in enumerate(self.lines):
            if content.startswith("> Last ID used:"):
                value = parse_id(content.split(":", 1)[1])
                if value is not None:
                    self.last_id = value
                    self.last_id_index = index
                continue
            if content.startswith("## "):
                heading = content[3:].strip()
                if self.path.endswith(ARCHIVE):
                    # Archive file: any section holds archived items.
                    bucket = "Archived"
                elif heading in BUCKETS:
                    bucket = heading
                else:
                    bucket = None
                    self.unknown_headings.append((index, heading))
                last_item = None
                continue
            if content.startswith("- ") and bucket is not None:
                item = Item(index, content, self.lines[index][1], bucket)
                self.items.append(item)
                last_item = item
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
            # Line indices of later items shift by one.
            for item in self.items:
                if item.line_index >= insert_at:
                    item.line_index += 1
            if self.last_id_index is not None and self.last_id_index >= insert_at:
                self.last_id_index += 1
        self.last_id = value

    def dominant_ending(self):
        for _content, ending in self.lines:
            if ending:
                return ending
        return "\n"

    def bucket_range(self, bucket):
        """Return (start_index, end_index) for a bucket section.

        start_index is the heading line; end_index is the next heading
        line, or the end of the file.
        """
        start = None
        end = len(self.lines)
        for index, (content, _ending) in enumerate(self.lines):
            if content == "## " + bucket:
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


def warnings_for(state, archive, directory=None):
    """Return a list of warning strings (never blocking)."""
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
    # Size budget.
    try:
        size = os.path.getsize(os.path.join(directory or os.getcwd(), STATE))
    except OSError:
        size = 0
    line_count = len(state.lines)
    if line_count > SIZE_LINE_LIMIT or size > SIZE_BYTE_LIMIT:
        warnings.append(
            "warning: %s is over the size budget (%d lines, %d bytes; "
            "budget %d lines / %d bytes)"
            % (STATE, line_count, size, SIZE_LINE_LIMIT, SIZE_BYTE_LIMIT)
        )
    # Stale Waiting on human items: unstarred dates older than 14 days.
    today = date.today()
    for item in state.items:
        if item.bucket != "Waiting on human":
            continue
        if item.item_date is None or item.starred:
            continue
        try:
            year, month, day = (int(part) for part in item.item_date.split("-"))
            item_date = date(year, month, day)
        except ValueError:
            continue
        if (today - item_date).days > STALE_DAYS:
            warnings.append(
                "stale: #%d in Waiting on human is %d days old "
                "(owner only, not a prompt to chase)"
                % (item.item_id, (today - item_date).days)
            )
    return warnings


def fail_cleanly(message):
    sys.stderr.write("error: %s\n" % message)
    sys.stderr.write("No files were changed. Fix the problem line or run statecat restore.\n")
    sys.exit(1)


def atomic_write(path, lines):
    """Write lines ([content, ending] pairs) atomically: temp, then rename."""
    directory = os.path.dirname(path) or "."
    handle, tmp_path = tempfile.mkstemp(dir=directory, prefix=".statecat-tmp-")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as out:
            for content, ending in lines:
                out.write(content + ending)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def atomic_write_bytes(path, data):
    directory = os.path.dirname(path) or "."
    handle, tmp_path = tempfile.mkstemp(dir=directory, prefix=".statecat-tmp-")
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(data)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def backup_root(directory):
    return os.path.join(directory, BACKUP_ROOT)


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
    """Keep at most 20 snapshots in total, counting safety snapshots.

    The most recent pre-write snapshot is never pruned, so the
    restore default always remains available.
    """
    root = backup_root(directory)
    if not os.path.isdir(root):
        return
    snapshots = list_snapshots(directory)
    if len(snapshots) <= BACKUPS_KEPT:
        return
    pre_write = [item for item in snapshots if item[1] == "pre-write"]
    latest_pre = pre_write[-1] if pre_write else None
    excess = len(snapshots) - BACKUPS_KEPT
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
    os.makedirs(root, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")
    name = ("safety-" + stamp) if kind == "safety" else stamp
    path = os.path.join(root, name)
    os.makedirs(path)
    for filename in (STATE, ARCHIVE):
        source = os.path.join(directory, filename)
        if os.path.exists(source):
            with open(source, "rb") as src, open(os.path.join(path, filename), "wb") as dst:
                shutil.copyfileobj(src, dst)
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
    project = args.project or os.path.basename(os.path.abspath(directory)) or "My Project"
    os.makedirs(backup_root(directory), exist_ok=True)
    atomic_write(state_path, lines_from_contents(template_state(project)))
    atomic_write(archive_path, lines_from_contents(template_archive(project)))
    print("created %s" % STATE)
    print("created %s" % ARCHIVE)
    return 0


def cmd_show(args):
    directory = require_root()
    state, _archive = read_state_and_archive(directory)
    if args.bucket:
        bucket = BUCKET_ALIASES.get(args.bucket.strip().lower())
        if bucket is None:
            sys.stderr.write("error: unknown bucket %r\n" % args.bucket)
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


def item_block_lines(state, item):
    """Return the raw lines for an item plus its continuations."""
    end = item.line_index + 1 + len(item.continuation_indices)
    return [list(pair) for pair in state.lines[item.line_index:end]]


def insert_block_before_heading(state, index, block_lines):
    """Insert a whole item block, leaving a blank line before a heading."""
    ending = state.dominant_ending()
    for offset, pair in enumerate(block_lines):
        state.lines.insert(index + offset, list(pair))
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
    state.lines.insert(index, [line_content, ending])
    if index + 1 < len(state.lines) and state.lines[index + 1][0].startswith("## "):
        if state.lines[index][0] != "":
            state.lines.insert(index + 1, ["", ending])
    return index


def insert_into_bucket(state, bucket, line_content):
    """Insert a line into a bucket section, just before the next heading."""
    start, end = state.bucket_range(bucket)
    if start is None:
        fail_cleanly("bucket %r not found in %s" % (bucket, STATE))
    insert_line_before_heading(state, end, line_content)
    return end


def cmd_add(args):
    directory = require_root()
    state, archive = read_state_and_archive(directory)
    validate_or_fail(state, archive)
    bucket = BUCKET_ALIASES.get(args.bucket.strip().lower())
    if bucket is None:
        sys.stderr.write("error: unknown bucket %r\n" % args.bucket)
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
        sys.stderr.write("error: no item #%d in %s\n" % (item_id, STATE))
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
    del state.lines[old_index:old_index + len(block)]
    # Find the target bucket in the shortened file.
    start, end = state.bucket_range(bucket)
    if start is None:
        fail_cleanly("bucket %r not found in %s" % (bucket, STATE))
    insert_block_before_heading(state, end, block)
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
        sys.stderr.write("error: unknown bucket %r\n" % args.bucket)
        return 1
    if item.bucket == "Parked" and not args.owner_approved:
        sys.stderr.write("error: #%d is Parked; moving out of Parked requires --owner-approved.\n" % item_id)
        return 1
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
    insert_line_before_heading(archive, end, line_content)


def append_block_to_archive(archive, block_lines):
    """Append a whole item block to the Archived section."""
    start, end = archive.bucket_range("Archived")
    if start is None:
        fail_cleanly(
            "archive file has no '## Archived' heading; "
            "refusing to create a headerless archive entry"
        )
    insert_block_before_heading(archive, end, block_lines)


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
    del state.lines[item.line_index:item.line_index + 1 + len(item.continuation_indices)]
    new_line = "- #%d [%s] %s" % (new_id, today_str(), new_text)
    insert_into_bucket(state, "Decided", new_line)
    atomic_write(os.path.join(directory, STATE), state.lines)
    print("superseded #%d with #%d" % (item_id, new_id))
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
    del state.lines[item.line_index:item.line_index + 1 + len(item.continuation_indices)]
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
        take_snapshot(directory, "pre-write")
        state, archive = read_state_and_archive(directory)
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
        if state.last_id is not None and highest > state.last_id:
            state.set_last_id(highest)
            counter_fixed = True
        if fixed or counter_fixed:
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
        match = None
        for name, kind, path in snapshots:
            if name == args.snapshot or name.startswith(args.snapshot):
                if match is not None:
                    sys.stderr.write("error: ambiguous snapshot %r\n" % args.snapshot)
                    return 1
                match = (name, kind, path)
        if match is None:
            sys.stderr.write("error: no snapshot %r\n" % args.snapshot)
            return 1
        target_name, _kind, target_path = match
    else:
        pre_write = [item for item in snapshots if item[1] == "pre-write"]
        if not pre_write:
            sys.stderr.write("error: no pre-write snapshot to restore\n")
            return 1
        target_name, _kind, target_path = pre_write[-1]
    # Safety snapshot first; it is never the default target.
    take_snapshot(directory, "safety")
    for filename in (STATE, ARCHIVE):
        source = os.path.join(target_path, filename)
        target = os.path.join(directory, filename)
        if os.path.exists(source):
            with open(source, "rb") as handle:
                data = handle.read()
            atomic_write_bytes(target, data)
    print("restored %s" % target_name)
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="statecat",
        description=(
            "StateCat v1: one living project state, readable by any agent "
            "in one read. Local only, plain Markdown, no network calls."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="Create STATE.md, STATE-ARCHIVE.md, and .statecat/backups/")
    p.add_argument("project", nargs="?", help="Project name for the About header")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("show", help="Print the full state, or one bucket")
    p.add_argument("bucket", nargs="?", help="Bucket name, e.g. Open")
    p.set_defaults(func=cmd_show)

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

    p = sub.add_parser("check", help="Report structural problems and warnings")
    p.add_argument("--fix", action="store_true", help="Assign missing IDs and starred dates (Parked-guard exempt)")
    p.add_argument("--fix-ids", dest="fix_ids", action="store_true", help="Alias for --fix")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("restore", help="Restore a paired snapshot of both files")
    p.add_argument("snapshot", nargs="?", help="Snapshot name, or omit for the most recent pre-write snapshot")
    p.add_argument("--list", action="store_true", help="List snapshots")
    p.set_defaults(func=cmd_restore)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
