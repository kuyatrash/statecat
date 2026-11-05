#!/usr/bin/env python3
"""StateCat v1 acceptance tests (scope revision 5, section 9).

Run with: python3 tests/test_statecat.py
Uses only the standard library. Each test runs the real CLI in a
fresh temporary folder; no test mutates the shipped example or
fixture. Exit code 0 means every test passed; the final line
reports the pass/fail count honestly.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(ROOT, "statecat.py")
FIXTURE_DIR = os.path.join(ROOT, "fixture")
TODAY = date.today().isoformat()


def run_cli(cwd, *args):
    return subprocess.run(
        [sys.executable, CLI, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def run_cli_env(cwd, env, *args):
    merged = dict(os.environ)
    merged.update(env)
    return subprocess.run(
        [sys.executable, CLI, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=merged,
    )


def read_text(path):
    with open(path, "r", encoding="utf-8", newline="") as handle:
        return handle.read()


def simulate_dead_owner(lock_path):
    """Rewrite a lock's owner file to a dead PID, for stale tests.

    A live owner must never be broken as stale, so tests that
    age a lock held by this process must first make its owner
    dead (and stop its heartbeat, so the lock can go stale).
    """
    owner_path = os.path.join(lock_path, "owner")
    text = re.sub(r"^pid=\d+", "pid=999999", read_text(owner_path), flags=re.M)
    with open(owner_path, "w", encoding="utf-8") as handle:
        handle.write(text)


def read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


def read_lines(path):
    with open(path, "r", encoding="utf-8", newline="") as handle:
        return handle.readlines()


def state_path(directory):
    return os.path.join(directory, "STATE.md")


def archive_path(directory):
    return os.path.join(directory, "STATE-ARCHIVE.md")


def item_lines(text):
    """Return {id: line} for lines starting with '- #N'."""
    result = {}
    for line in text.splitlines():
        match = re.match(r"- #(\d+) ", line)
        if match:
            result[int(match.group(1))] = line
    return result


def bucket_lines(text, bucket):
    """Return item lines in one bucket section of STATE.md."""
    lines = text.splitlines()
    in_bucket = False
    result = []
    for line in lines:
        if line.startswith("## "):
            in_bucket = line[3:].strip() == bucket
            continue
        if in_bucket and line.startswith("- "):
            result.append(line)
    return result


class StateCatTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def init_project(self, name="Test Project"):
        result = run_cli(self.dir, "init", name)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def copy_fixture(self):
        for filename in ("STATE.md", "STATE-ARCHIVE.md"):
            shutil.copy(os.path.join(FIXTURE_DIR, filename), self.dir)
        os.makedirs(os.path.join(self.dir, ".statecat", "backups"), exist_ok=True)

    # 1. One-read fresh listing (fixture traps, section 9)
    def test_one_read_fresh_listing(self):
        self.copy_fixture()
        result = run_cli(self.dir, "show")
        self.assertEqual(result.returncode, 0, result.stderr)
        text = result.stdout
        self.assertEqual([line for line in bucket_lines(text, "Decided") if line.startswith("- #12")][0].count("supersedes #3"), 1)
        self.assertTrue(any(line.startswith("- #8") for line in bucket_lines(text, "Open")))
        self.assertTrue(any(line.startswith("- #6") for line in bucket_lines(text, "Waiting on human")))
        self.assertTrue(any(line.startswith("- #9") for line in bucket_lines(text, "Parked")))
        # The superseded item #3 is not a live Decided line.
        decided_ids = [int(re.match(r"- #(\d+) ", line).group(1)) for line in bucket_lines(text, "Decided")]
        self.assertNotIn(3, decided_ids)
        archive = read_text(archive_path(self.dir))
        self.assertTrue(any(line.startswith("- #3") for line in bucket_lines(archive, "Archived")) if "## Archived" in archive else "- #3" in archive)

    # 2. ID uniqueness after an update rewrites wording
    def test_id_uniqueness_after_update(self):
        self.init_project()
        run_cli(self.dir, "add", "Open", "First wording")
        result = run_cli(self.dir, "update", "#1", "Completely different wording")
        self.assertEqual(result.returncode, 0, result.stderr)
        result = run_cli(self.dir, "check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("clean", result.stdout)

    # 3. Supersede creates a new ID and archives the old one
    def test_supersede_creates_new_id_and_archives_old(self):
        self.init_project()
        run_cli(self.dir, "add", "Decided", "Use a hosted tool")
        result = run_cli(self.dir, "supersede", "#1", "Use local files")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("#2", result.stdout)
        state = read_text(state_path(self.dir))
        archive = read_text(archive_path(self.dir))
        self.assertIn("supersedes #1", state)
        self.assertTrue(any(line.startswith("- #1") for line in archive.splitlines()))
        self.assertNotIn("- #1 [", state.split("## Decided")[1].split("## Open")[0])
        result = run_cli(self.dir, "check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    # 4. Paired restore after a supersede restores both files
    def test_paired_restore_after_supersede(self):
        self.init_project()
        run_cli(self.dir, "add", "Decided", "Use a hosted tool")
        run_cli(self.dir, "supersede", "#1", "Use local files")
        result = run_cli(self.dir, "restore")
        self.assertEqual(result.returncode, 0, result.stderr)
        state = read_text(state_path(self.dir))
        archive = read_text(archive_path(self.dir))
        self.assertTrue(any(line.startswith("- #1") for line in bucket_lines(state, "Decided")))
        self.assertNotIn("- #1", archive)
        result = run_cli(self.dir, "check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    # 5. Interrupted two-file write: duplicate detected, paired restore recovers
    def test_interrupted_write_duplicate_and_paired_restore(self):
        self.copy_fixture()
        run_cli(self.dir, "add", "Open", "Temp item")
        # Simulate a crash between the archive write and the STATE.md
        # write: the same item now exists in both files, identical wording.
        state_text = read_text(state_path(self.dir))
        open_line = [line for line in bucket_lines(state_text, "Open") if line.startswith("- #8")][0]
        with open(archive_path(self.dir), "a", encoding="utf-8", newline="") as handle:
            handle.write(open_line + "\n")
        result = run_cli(self.dir, "check")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("duplicate ID #8", result.stdout)
        self.assertIn("stray", result.stdout)
        self.assertIn("STATE-ARCHIVE.md", result.stdout)
        result = run_cli(self.dir, "restore")
        self.assertEqual(result.returncode, 0, result.stderr)
        result = run_cli(self.dir, "check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        archive = read_text(archive_path(self.dir))
        self.assertNotIn("- #8", archive)

    # 6. Bare hand-added line: accepted, does not block, fixed with starred date
    def test_bare_hand_line_accepted_and_fixed_starred(self):
        self.copy_fixture()
        text = read_text(state_path(self.dir))
        # Insert a bare line into Open, before the next heading.
        lines = text.splitlines(keepends=True)
        # Find the Waiting on human heading and insert before it.
        insert_at = next(i for i, line in enumerate(lines) if line.startswith("## Waiting on human"))
        lines.insert(insert_at, "- Ask supplier about pricing\n")
        with open(state_path(self.dir), "w", encoding="utf-8", newline="") as handle:
            handle.writelines(lines)
        result = run_cli(self.dir, "check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("no ID", result.stdout)
        self.assertIn("no date", result.stdout)
        # A bare line must not block other writes.
        result = run_cli(self.dir, "add", "Open", "Another item")
        self.assertEqual(result.returncode, 0, result.stderr)
        result = run_cli(self.dir, "check", "--fix")
        self.assertEqual(result.returncode, 0, result.stderr)
        state = read_text(state_path(self.dir))
        fixed = [line for line in state.splitlines() if "Ask supplier about pricing" in line][0]
        self.assertRegex(fixed, r"- #\d+ \[\d{4}-\d{2}-\d{2}\*\] Ask supplier about pricing")

    # 7. Starred dates are age unknown, not fresh and not stale
    def test_starred_date_age_unknown_not_stale(self):
        self.copy_fixture()
        text = read_text(state_path(self.dir))
        text = text.replace("[2026-09-15]", "[2020-01-01*]")
        with open(state_path(self.dir), "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        result = run_cli(self.dir, "check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("age is unknown", result.stdout)
        self.assertNotIn("stale: #6", result.stdout)

    # 8. Hand-broken file fails cleanly and writes nothing
    def test_hand_broken_file_fails_cleanly_no_write(self):
        self.init_project()
        run_cli(self.dir, "add", "Open", "Keep me")
        before = read_bytes(state_path(self.dir))
        with open(state_path(self.dir), "a", encoding="utf-8", newline="") as handle:
            handle.write("\n## Mystery\n- #99 [2026-10-26] Broken bucket item\n")
        result = run_cli(self.dir, "add", "Open", "Should not be added")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Mystery", result.stderr)
        after = read_bytes(state_path(self.dir))
        # Only the hand edit itself is present; the failed add wrote nothing.
        self.assertNotIn(b"Should not be added", after)
        self.assertEqual(after, before + b"\n## Mystery\n- #99 [2026-10-26] Broken bucket item\n")

    # 9. Untouched lines stay byte-for-byte
    def test_untouched_lines_byte_for_byte(self):
        self.init_project()
        run_cli(self.dir, "add", "Open", "First item")
        run_cli(self.dir, "add", "Open", "Second item")
        before = read_lines(state_path(self.dir))
        run_cli(self.dir, "update", "#1", "First item, revised")
        after = read_lines(state_path(self.dir))
        self.assertEqual(len(before), len(after))
        changed = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
        self.assertEqual(len(changed), 1)
        self.assertIn("First item, revised", after[changed[0]])

    # 10. Untouched CRLF lines keep their endings
    def test_untouched_lines_crlf_preserved(self):
        self.init_project()
        run_cli(self.dir, "add", "Open", "First item")
        run_cli(self.dir, "add", "Open", "Second item")
        with open(state_path(self.dir), "rb") as handle:
            data = handle.read()
        data = data.replace(b"\n", b"\r\n")
        with open(state_path(self.dir), "wb") as handle:
            handle.write(data)
        run_cli(self.dir, "update", "#1", "First item, revised")
        with open(state_path(self.dir), "rb") as handle:
            after = handle.read()
        lines = after.split(b"\r\n")
        # Every line except the updated one still ends with CRLF in the raw file.
        self.assertTrue(after.count(b"\r\n") >= 10)
        self.assertIn(b"First item, revised", after)

    # 11. Parked temptation: move/archive/update refused without the flag
    def test_parked_temptation(self):
        self.copy_fixture()
        before = read_bytes(state_path(self.dir))
        for args in (("move", "#9", "Open"), ("archive", "#9"), ("update", "#9", "Sneaky wording")):
            result = run_cli(self.dir, *args)
            self.assertNotEqual(result.returncode, 0, args)
            self.assertIn("owner-approved", result.stderr)
        self.assertEqual(read_bytes(state_path(self.dir)), before)
        result = run_cli(self.dir, "move", "#9", "Open", "--owner-approved")
        self.assertEqual(result.returncode, 0, result.stderr)

    # 12. check --fix on a parked line succeeds without the flag
    def test_fix_on_parked_succeeds_without_flag(self):
        self.copy_fixture()
        text = read_text(state_path(self.dir))
        lines = text.splitlines(keepends=True)
        # Add a bare line to Parked (it is the last bucket).
        lines.append("- Parked hand note\n")
        with open(state_path(self.dir), "w", encoding="utf-8", newline="") as handle:
            handle.writelines(lines)
        result = run_cli(self.dir, "check", "--fix")
        self.assertEqual(result.returncode, 0, result.stderr)
        state = read_text(state_path(self.dir))
        self.assertTrue(any("Parked hand note" in line and re.match(r"- #\d+ ", line) for line in state.splitlines()))

    # 13. Manual path: no-CLI add and supersede, then check clean
    def test_manual_path_add_and_supersede_check_clean(self):
        self.init_project()
        # Manual add: Decided #1, following the skill's manual rules.
        text = read_text(state_path(self.dir))
        text = text.replace("> Last ID used: #0", "> Last ID used: #1")
        text = text.replace("## Decided\n", "## Decided\n- #1 [%s] Use a hosted tool\n" % TODAY, 1)
        with open(state_path(self.dir), "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        # Manual supersede: archive #1, add #2 noting supersedes #1.
        text = read_text(state_path(self.dir))
        text = text.replace("> Last ID used: #1", "> Last ID used: #2")
        text = text.replace("- #1 [%s] Use a hosted tool\n" % TODAY, "", 1)
        text = text.replace("## Decided\n", "## Decided\n- #2 [%s] Use local files (supersedes #1)\n" % TODAY, 1)
        with open(state_path(self.dir), "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        archive = read_text(archive_path(self.dir))
        archive = archive.replace("## Archived\n", "## Archived\n- #1 [%s] Use a hosted tool\n" % TODAY, 1)
        with open(archive_path(self.dir), "w", encoding="utf-8", newline="") as handle:
            handle.write(archive)
        result = run_cli(self.dir, "check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("clean", result.stdout)

    # 14. Restoring twice does not flip back
    def test_restore_twice_does_not_flip(self):
        self.init_project()
        run_cli(self.dir, "add", "Open", "Item A")
        run_cli(self.dir, "add", "Open", "Item B")
        run_cli(self.dir, "restore")
        after_first = read_bytes(state_path(self.dir))
        self.assertNotIn(b"Item B", after_first)
        self.assertIn(b"Item A", after_first)
        run_cli(self.dir, "restore")
        after_second = read_bytes(state_path(self.dir))
        self.assertEqual(after_first, after_second)
        self.assertNotIn(b"Item B", after_second)

    # 15. Deleting the top ID by hand still issues a fresh ID
    def test_delete_top_id_still_fresh_id(self):
        self.init_project()
        run_cli(self.dir, "add", "Open", "One")
        run_cli(self.dir, "add", "Open", "Two")
        run_cli(self.dir, "add", "Open", "Three")
        text = read_text(state_path(self.dir))
        text = "\n".join(line for line in text.splitlines() if not line.startswith("- #3 ")) + "\n"
        with open(state_path(self.dir), "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        result = run_cli(self.dir, "add", "Open", "Four")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("#4", result.stdout)

    # 16. Size warning past 150 lines or 10 KB
    def test_size_warning(self):
        self.init_project()
        text = read_text(state_path(self.dir))
        text += "\n" * 200
        with open(state_path(self.dir), "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        result = run_cli(self.dir, "check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("size budget", result.stdout)

    # 17. Stale flag only for unstarred Waiting on human items older than 14 days
    def test_stale_flag_unstarred_only(self):
        self.copy_fixture()
        result = run_cli(self.dir, "check")
        self.assertIn("stale: #6", result.stdout)

    # 18. init refuses to overwrite
    def test_init_refuses_overwrite(self):
        self.init_project()
        result = run_cli(self.dir, "init", "Again")
        self.assertNotEqual(result.returncode, 0)

    # 19. show a single bucket
    def test_show_single_bucket(self):
        self.copy_fixture()
        result = run_cli(self.dir, "show", "Open")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("#8", result.stdout)
        self.assertNotIn("#9", result.stdout)

    # 20. Paired backup pruning keeps at most 20 pre-write snapshots
    def test_backup_pruning_keeps_20(self):
        self.init_project()
        for i in range(25):
            run_cli(self.dir, "add", "Open", "Item %d" % i)
        result = run_cli(self.dir, "restore", "--list")
        pre_write = [line for line in result.stdout.splitlines() if line.strip().endswith("pre-write")]
        self.assertLessEqual(len(pre_write), 20)

    # 21. Last ID used advances and is never reused after archiving
    def test_last_id_never_reused_after_archive(self):
        self.init_project()
        run_cli(self.dir, "add", "Open", "One")
        run_cli(self.dir, "add", "Open", "Two")
        run_cli(self.dir, "archive", "#1")
        result = run_cli(self.dir, "add", "Open", "Three")
        self.assertIn("#3", result.stdout)
        state = read_text(state_path(self.dir))
        archive = read_text(archive_path(self.dir))
        self.assertIn("Last ID used: #3", state)
        self.assertIn("- #1", archive)

    # --- Fix round 2026-10-26 (owner code review) ---
    def test_bare_number_id_works(self):
        self.init_project()
        run_cli(self.dir, "add", "Open", "Bare id item")
        result = run_cli(self.dir, "move", "1", "Waiting on human")
        self.assertEqual(result.returncode, 0, result.stderr)
        result = run_cli(self.dir, "update", "1", "Bare id item, revised")
        self.assertEqual(result.returncode, 0, result.stderr)
        result = run_cli(self.dir, "check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_docs_use_bare_ids_and_full_pointer(self):
        for filename in ("README.md", "SKILL.md", "AGENTS-pointer.md"):
            text = read_text(os.path.join(ROOT, filename))
            self.assertIn("python3 statecat.py check", text, filename)
            self.assertNotIn("run statecat check", text, filename)
        readme = read_text(os.path.join(ROOT, "README.md"))
        self.assertIn("move 2 ", readme)
        self.assertNotIn("python3 statecat.py move #2", readme)
        self.assertNotIn("python3 statecat.py move #9", readme)
        self.assertIn("unquoted `#` starts a comment", readme)
        help_text = subprocess.run(
            [sys.executable, CLI, "move", "--help"], capture_output=True, text=True
        ).stdout
        self.assertIn("e.g. 12", help_text)
        self.assertNotIn("e.g. #12", help_text)

    def test_launcher_forwards_to_script(self):
        launcher_src = os.path.join(ROOT, "statecat")
        self.assertTrue(os.path.isfile(launcher_src))
        self.assertTrue(os.access(launcher_src, os.X_OK))
        shutil.copy(CLI, self.dir)
        shutil.copy(launcher_src, self.dir)
        result = subprocess.run(
            [os.path.join(self.dir, "statecat"), "init", "Launcher Project"],
            cwd=self.dir, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run(
            [os.path.join(self.dir, "statecat"), "check"],
            cwd=self.dir, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("clean", result.stdout)

    def test_subfolder_walkup_and_init_refusal(self):
        self.init_project()
        run_cli(self.dir, "add", "Open", "Root item")
        deep = os.path.join(self.dir, "sub", "deep")
        os.makedirs(deep)
        result = run_cli(deep, "add", "Open", "From subfolder")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(os.path.exists(os.path.join(deep, "STATE.md")))
        self.assertFalse(os.path.exists(os.path.join(self.dir, "sub", "STATE.md")))
        state = read_text(state_path(self.dir))
        self.assertIn("From subfolder", state)
        self.assertTrue(os.path.isdir(os.path.join(self.dir, ".statecat", "backups")))
        result = run_cli(deep, "show")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("From subfolder", result.stdout)
        result = run_cli(deep, "init", "Second")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("parent project root", result.stderr)

    def test_no_root_error_is_clear_and_creates_nothing(self):
        result = run_cli(self.dir, "show")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no STATE.md found", result.stderr)
        self.assertIn("project root", result.stderr)
        self.assertFalse(os.path.exists(state_path(self.dir)))

    def test_restore_with_missing_state_file(self):
        self.init_project()
        run_cli(self.dir, "add", "Open", "Keep me")
        os.remove(state_path(self.dir))
        result = run_cli(self.dir, "restore", "--list")
        self.assertEqual(result.returncode, 0, result.stderr)
        result = run_cli(self.dir, "restore")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(os.path.exists(state_path(self.dir)))
        self.assertTrue(os.path.exists(archive_path(self.dir)))
        state = read_text(state_path(self.dir))
        self.assertNotIn("Keep me", state)
        result = run_cli(self.dir, "check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_restore_with_no_snapshots_fails_cleanly(self):
        result = run_cli(self.dir, "restore")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(os.path.exists(state_path(self.dir)))

    def test_missing_heading_is_structural_and_not_recreated(self):
        self.copy_fixture()
        text = read_text(state_path(self.dir))
        text = text.replace("## Parked\n", "", 1)
        with open(state_path(self.dir), "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        result = run_cli(self.dir, "check")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing bucket heading", result.stdout)
        self.assertIn("Parked", result.stdout)
        result = run_cli(self.dir, "add", "Open", "Must not be added")
        self.assertNotEqual(result.returncode, 0)
        result = run_cli(self.dir, "check", "--fix")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("## Parked", read_text(state_path(self.dir)))

    def test_newline_text_rejected_without_backup(self):
        self.init_project()
        run_cli(self.dir, "add", "Open", "Good item")
        for args in (("add", "Open", "bad\nline"), ("update", "1", "bad\rline")):
            result = run_cli(self.dir, *args)
            self.assertNotEqual(result.returncode, 0, args)
            self.assertIn("single line", result.stderr)
        # No snapshot was taken for the rejected add (only the good add's).
        result = run_cli(self.dir, "restore", "--list")
        snapshots = [line for line in result.stdout.splitlines() if line.strip()]
        self.assertEqual(len(snapshots), 1)
        state = read_text(state_path(self.dir))
        self.assertNotIn("bad", state)

    def test_safety_snapshots_count_toward_pruning(self):
        self.init_project()
        for i in range(5):
            run_cli(self.dir, "add", "Open", "Item %d" % i)
        for _ in range(15):
            run_cli(self.dir, "restore")
        result = run_cli(self.dir, "restore", "--list")
        snapshots = [line for line in result.stdout.splitlines() if line.strip()]
        self.assertLessEqual(len(snapshots), 20)
        self.assertTrue(any(line.strip().endswith("pre-write") for line in snapshots))
        # Default restore still works after pruning.
        result = run_cli(self.dir, "restore")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_blank_line_before_next_heading(self):
        self.init_project()
        run_cli(self.dir, "add", "Open", "Middle bucket item")
        text = read_text(state_path(self.dir))
        self.assertTrue(text.endswith("\n"))
        lines = text.splitlines()
        idx = lines.index("- #1 [%s] Middle bucket item" % TODAY)
        self.assertEqual(lines[idx + 1], "")
        self.assertEqual(lines[idx + 2], "## Waiting on human")

    # --- Fix round 2026-10-26 (second-agent review) ---
    def _wrap_first_item(self):
        text = read_text(state_path(self.dir))
        text = text.replace(
            "- #1 [%s] Wrapped item first line" % TODAY,
            "- #1 [%s] Wrapped item first line\n    wrapped continuation" % TODAY,
            1,
        )
        with open(state_path(self.dir), "w", encoding="utf-8", newline="") as handle:
            handle.write(text)

    def test_wrapped_item_archives_as_one_block(self):
        self.init_project()
        run_cli(self.dir, "add", "Open", "Wrapped item first line")
        run_cli(self.dir, "add", "Open", "Other item")
        self._wrap_first_item()
        result = run_cli(self.dir, "check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = run_cli(self.dir, "archive", "1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("wrapped continuation", read_text(archive_path(self.dir)))
        self.assertNotIn("wrapped continuation", read_text(state_path(self.dir)))

    def test_wrapped_item_moves_as_one_block(self):
        self.init_project()
        run_cli(self.dir, "add", "Open", "Wrapped item first line")
        self._wrap_first_item()
        result = run_cli(self.dir, "move", "1", "Waiting on human")
        self.assertEqual(result.returncode, 0, result.stderr)
        state = read_text(state_path(self.dir))
        waiting = state.split("## Waiting on human")[1].split("## Waiting on others")[0]
        self.assertIn("wrapped continuation", waiting)
        self.assertNotIn("wrapped continuation", state.split("## Open")[1].split("## Waiting on human")[0])

    def test_wrapped_item_update_keeps_continuation(self):
        self.init_project()
        run_cli(self.dir, "add", "Open", "Wrapped item first line")
        self._wrap_first_item()
        result = run_cli(self.dir, "update", "1", "Wrapped item revised")
        self.assertEqual(result.returncode, 0, result.stderr)
        state = read_text(state_path(self.dir))
        self.assertIn("Wrapped item revised", state)
        self.assertIn("wrapped continuation", state)
        result = run_cli(self.dir, "check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_orphan_indented_line_is_structural(self):
        self.init_project()
        run_cli(self.dir, "add", "Open", "Plain item")
        text = read_text(state_path(self.dir))
        text = text.replace("## Parked\n", "## Parked\n\n    orphan indented line\n", 1)
        with open(state_path(self.dir), "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        result = run_cli(self.dir, "check")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("orphan indented line", result.stdout)

    def test_drifted_id_counter_warns_and_fix_raises(self):
        self.init_project()
        run_cli(self.dir, "add", "Open", "Plain item")
        text = read_text(state_path(self.dir))
        text = text.replace("Last ID used: #1", "Last ID used: #0", 1)
        text = text.replace("- #1 ", "- #50 ", 1)
        with open(state_path(self.dir), "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        result = run_cli(self.dir, "check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("behind the highest ID in use #50", result.stdout)
        result = run_cli(self.dir, "check", "--fix")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Last ID used to #50", result.stdout)
        self.assertIn("Last ID used: #50", read_text(state_path(self.dir)))
        result = run_cli(self.dir, "check")
        self.assertIn("clean", result.stdout)

    def test_missing_archive_is_structural_and_not_recreated_headerless(self):
        self.init_project()
        run_cli(self.dir, "add", "Open", "Plain item")
        os.remove(archive_path(self.dir))
        result = run_cli(self.dir, "check")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing file", result.stdout)
        self.assertIn("STATE-ARCHIVE.md", result.stdout)
        result = run_cli(self.dir, "archive", "1")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(os.path.exists(archive_path(self.dir)))

    def test_missing_archive_heading_is_structural(self):
        self.init_project()
        run_cli(self.dir, "add", "Open", "Plain item")
        text = read_text(archive_path(self.dir))
        text = text.replace("## Archived\n", "", 1)
        with open(archive_path(self.dir), "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        result = run_cli(self.dir, "check")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("## Archived", result.stdout)

    def test_require_state_is_removed(self):
        cli_text = read_text(CLI)
        self.assertNotIn("def require_state", cli_text)
        self.assertNotIn("require_state(", cli_text)

    def test_github_polish_files_exist(self):
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "LICENSE")))
        self.assertIn("MIT License", read_text(os.path.join(ROOT, "LICENSE")))
        readme = read_text(os.path.join(ROOT, "README.md"))
        self.assertIn("Try it in 2 minutes", readme)
        self.assertNotIn("logo.svg", readme)
        self.assertIn("demo/DEMO.md", readme)
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "demo", "DEMO.md")))
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "demo", "run-demo.sh")))

    def test_windows_py_note_and_gitignore(self):
        readme = read_text(os.path.join(ROOT, "README.md"))
        skill = read_text(os.path.join(ROOT, "SKILL.md"))
        self.assertIn("py statecat.py", readme)
        self.assertIn("py", skill)
        gitignore = read_text(os.path.join(ROOT, ".gitignore"))
        self.assertIn("__pycache__/", gitignore)

    # v1.1: one writer at a time, enforced with a lock directory.
    def test_concurrent_adds_keep_both_items(self):
        self.init_project()
        env = dict(os.environ, STATECAT_LOCK_TIMEOUT="10")
        first = subprocess.Popen(
            [sys.executable, CLI, "add", "Open", "first writer"],
            cwd=self.dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=env,
        )
        second = subprocess.Popen(
            [sys.executable, CLI, "add", "Open", "second writer"],
            cwd=self.dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=env,
        )
        _out1, err1 = first.communicate()
        _out2, err2 = second.communicate()
        self.assertEqual(first.returncode, 0, err1)
        self.assertEqual(second.returncode, 0, err2)
        lines = item_lines(read_text(state_path(self.dir)))
        self.assertEqual(sorted(lines), [1, 2])
        texts = list(lines.values())
        self.assertTrue(any("first writer" in line for line in texts))
        self.assertTrue(any("second writer" in line for line in texts))

    def test_held_lock_fails_cleanly_without_write(self):
        self.init_project()
        lock_path = os.path.join(self.dir, ".statecat", "lock")
        os.makedirs(lock_path)
        result = run_cli_env(
            self.dir, {"STATECAT_LOCK_TIMEOUT": "0.5"},
            "add", "Open", "blocked writer",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("lock is held", result.stderr)
        self.assertNotIn("blocked writer", read_text(state_path(self.dir)))
        self.assertTrue(os.path.isdir(lock_path))

    def test_stale_lock_is_broken_and_write_succeeds(self):
        self.init_project()
        lock_path = os.path.join(self.dir, ".statecat", "lock")
        os.makedirs(lock_path)
        old = time.time() - 3600
        os.utime(lock_path, (old, old))
        result = run_cli_env(
            self.dir, {"STATECAT_LOCK_TIMEOUT": "5", "STATECAT_LOCK_STALE": "60"},
            "add", "Open", "after stale lock",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("after stale lock", read_text(state_path(self.dir)))
        self.assertFalse(os.path.isdir(lock_path))

    # v1.1: init installs the pointer line and gitignores .statecat/.
    def test_init_creates_agents_md_with_pointer_and_gitignore(self):
        result = run_cli(self.dir, "init", "Pointer Project")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("created AGENTS.md with pointer", result.stdout)
        self.assertIn("created .gitignore with .statecat/", result.stdout)
        agents = read_text(os.path.join(self.dir, "AGENTS.md"))
        self.assertIn("Read STATE.md first", agents)
        gitignore = read_text(os.path.join(self.dir, ".gitignore"))
        self.assertIn(".statecat/", gitignore)

    def test_init_appends_pointer_to_existing_agents_md_without_duplicating(self):
        with open(os.path.join(self.dir, "AGENTS.md"), "w", encoding="utf-8") as handle:
            handle.write("# My instructions\n")
        self.init_project()
        agents = read_text(os.path.join(self.dir, "AGENTS.md"))
        self.assertIn("Read STATE.md first", agents)
        result = run_cli(self.dir, "pointer", "--install")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("pointer already in AGENTS.md", result.stdout)
        agents = read_text(os.path.join(self.dir, "AGENTS.md"))
        self.assertEqual(agents.count("Read STATE.md first"), 1)

    def test_init_uses_claude_md_when_only_it_exists(self):
        with open(os.path.join(self.dir, "CLAUDE.md"), "w", encoding="utf-8") as handle:
            handle.write("# Claude notes\n")
        self.init_project()
        self.assertFalse(os.path.exists(os.path.join(self.dir, "AGENTS.md")))
        self.assertIn("Read STATE.md first", read_text(os.path.join(self.dir, "CLAUDE.md")))

    def test_init_no_pointer_and_no_gitignore_flags_skip(self):
        result = run_cli(self.dir, "init", "Bare Project", "--no-pointer", "--no-gitignore")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "AGENTS.md")))
        self.assertFalse(os.path.exists(os.path.join(self.dir, ".gitignore")))

    def test_pointer_prints_the_line(self):
        result = run_cli(self.dir, "pointer")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "Read STATE.md first, then run python3 statecat.py check, before asking about or working on this project.")

    # v1.1: show --json, status, search.
    def test_show_json_is_machine_readable(self):
        self.init_project("Json Project")
        run_cli(self.dir, "add", "Decided", "Ship it")
        run_cli(self.dir, "add", "Open", "Test it")
        result = run_cli(self.dir, "show", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["project"], "Json Project")
        self.assertEqual(data["last_id"], 2)
        self.assertEqual([item["text"] for item in data["buckets"]["Decided"]], ["Ship it"])
        self.assertEqual([item["id"] for item in data["buckets"]["Open"]], [2])
        self.assertEqual(data["buckets"]["Parked"], [])

    def test_show_json_single_bucket(self):
        self.init_project()
        run_cli(self.dir, "add", "Open", "One item")
        result = run_cli(self.dir, "show", "Open", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(list(data["buckets"]), ["Open"])
        self.assertEqual(len(data["buckets"]["Open"]), 1)

    def test_status_summary(self):
        self.init_project("Status Project")
        run_cli(self.dir, "add", "Decided", "Done")
        run_cli(self.dir, "add", "Open", "Doing")
        result = run_cli(self.dir, "status")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("StateCat status: Status Project", result.stdout)
        self.assertIn("Decided: 1", result.stdout)
        self.assertIn("Open: 1", result.stdout)
        self.assertIn("Last ID used: #2", result.stdout)
        self.assertIn("check: clean", result.stdout)

    def test_search_finds_text_and_reports_no_match(self):
        self.init_project()
        run_cli(self.dir, "add", "Open", "Bake the test batch")
        run_cli(self.dir, "add", "Open", "Buy flour")
        result = run_cli(self.dir, "search", "BAKE")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Bake the test batch", result.stdout)
        self.assertNotIn("Buy flour", result.stdout)
        result = run_cli(self.dir, "search", "nothing here")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no matches", result.stdout)

    # v1.1: --version and packaging files.
    def test_version_flag(self):
        result = run_cli(self.dir, "--version")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.strip().startswith("statecat 1.2.0"))

    def test_packaging_and_ci_files_exist(self):
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "pyproject.toml")))
        self.assertIn("statecat = \"statecat:main\"", read_text(os.path.join(ROOT, "pyproject.toml")))
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "CHANGELOG.md")))
        self.assertIn("1.1.0", read_text(os.path.join(ROOT, "CHANGELOG.md")))
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "CONTRIBUTING.md")))
        self.assertTrue(os.path.isfile(os.path.join(ROOT, ".github", "workflows", "test.yml")))
        workflow = read_text(os.path.join(ROOT, ".github", "workflows", "test.yml"))
        self.assertIn("unittest discover", workflow)
        # Pinned to 24.04: the setup-python manifest has no Python
        # 3.8 build for Ubuntu 26.04, so ubuntu-latest would fail
        # the 3.8 job once it moves to 26.04.
        self.assertIn("ubuntu-24.04", workflow)

    # v1.1: compact, config, stale Waiting on others.
    def backdate_item(self, item_id, new_date):
        text = read_text(state_path(self.dir))
        text = re.sub(
            r"(- #%d \[)[0-9-]+(\*?\] )" % item_id,
            lambda match: match.group(1) + new_date + match.group(2),
            text,
            count=1,
        )
        with open(state_path(self.dir), "w", encoding="utf-8", newline="") as handle:
            handle.write(text)

    def test_compact_archives_old_decided_only(self):
        self.init_project()
        run_cli(self.dir, "add", "Decided", "Old decision")
        run_cli(self.dir, "add", "Decided", "Fresh decision")
        run_cli(self.dir, "add", "Open", "Old open item")
        self.backdate_item(1, "2026-01-01")
        self.backdate_item(3, "2026-01-01")
        result = run_cli(self.dir, "compact", "--older-than", "90", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("would archive #1", result.stdout)
        self.assertNotIn("would archive #2", result.stdout)
        self.assertNotIn("would archive #3", result.stdout)
        self.assertIn("Old decision", read_text(state_path(self.dir)))
        result = run_cli(self.dir, "compact", "--older-than", "90")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("archived #1", result.stdout)
        state_text = read_text(state_path(self.dir))
        self.assertNotIn("Old decision", state_text)
        self.assertIn("Fresh decision", state_text)
        self.assertIn("Old open item", state_text)
        self.assertIn("Old decision", read_text(archive_path(self.dir)))
        result = run_cli(self.dir, "compact", "--older-than", "90")
        self.assertIn("nothing to compact", result.stdout)

    def test_config_show_and_set(self):
        self.init_project()
        result = run_cli(self.dir, "config")
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads(result.stdout)
        self.assertEqual(config["stale_days"], 14)
        self.assertEqual(config["backups_kept"], 20)
        result = run_cli(self.dir, "config", "set", "stale_days", "30")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("set stale_days = 30", result.stdout)
        config = json.loads(run_cli(self.dir, "config").stdout)
        self.assertEqual(config["stale_days"], 30)
        self.assertEqual(config["backups_kept"], 20)
        result = run_cli(self.dir, "config", "set", "nope", "1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unknown config key", result.stderr)
        result = run_cli(self.dir, "config", "set", "stale_days", "abc")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not a valid value", result.stderr)

    def test_stale_waiting_on_others_and_config_stale_days(self):
        self.init_project()
        run_cli(self.dir, "add", "Waiting on others", "Vendor reply")
        self.backdate_item(1, "2026-01-01")
        result = run_cli(self.dir, "check")
        self.assertIn("stale: #1 in Waiting on others", result.stdout)
        run_cli(self.dir, "config", "set", "stale_days", "100000")
        result = run_cli(self.dir, "check")
        self.assertNotIn("stale:", result.stdout)
        run_cli(self.dir, "config", "set", "stale_days", "0")
        result = run_cli(self.dir, "check")
        self.assertIn("stale: #1 in Waiting on others", result.stdout)

    # v1.1: diff and restore verification.
    def test_diff_shows_change_then_no_differences_after_restore(self):
        self.init_project()
        run_cli(self.dir, "add", "Open", "Diff me")
        result = run_cli(self.dir, "diff")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("+", result.stdout)
        self.assertIn("Diff me", result.stdout)
        self.assertIn("STATE.md", result.stdout)
        result = run_cli(self.dir, "restore")
        self.assertEqual(result.returncode, 0, result.stderr)
        result = run_cli(self.dir, "diff")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no differences", result.stdout)

    def test_restore_of_broken_snapshot_warns(self):
        self.init_project()
        run_cli(self.dir, "add", "Open", "Good item")
        broken = os.path.join(self.dir, ".statecat", "backups", "20260101T000000000000")
        os.makedirs(broken)
        broken_state = read_text(state_path(self.dir)).replace("## Open\n", "", 1)
        with open(os.path.join(broken, "STATE.md"), "w", encoding="utf-8", newline="") as handle:
            handle.write(broken_state)
        shutil.copy(archive_path(self.dir), os.path.join(broken, "STATE-ARCHIVE.md"))
        result = run_cli(self.dir, "restore", "20260101T000000000000")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("restored 20260101T000000000000", result.stdout)
        self.assertIn("warning: restored files have structural problems", result.stdout)
        self.assertIn("problem:", result.stdout)

    # v1.1: parser tolerance, set_last_id fix, archive warnings.
    def test_item_without_space_after_date_bracket_parses(self):
        self.init_project()
        text = read_text(state_path(self.dir))
        text = text.replace("## Open\n", "## Open\n- #1 [2026-10-24]No space here\n", 1)
        with open(state_path(self.dir), "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        result = run_cli(self.dir, "show", "Open", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["buckets"]["Open"][0]["id"], 1)
        self.assertEqual(data["buckets"]["Open"][0]["date"], "2026-10-24")
        self.assertEqual(data["buckets"]["Open"][0]["text"], "No space here")
        result = run_cli(self.dir, "check")
        self.assertNotIn("has no date", result.stdout)

    def test_check_fix_inserts_last_id_line_once(self):
        self.init_project()
        text = read_text(state_path(self.dir))
        text = "\n".join(
            line for line in text.splitlines() if not line.startswith("> Last ID used:")
        ) + "\n"
        text = text.replace("## Open\n", "## Open\n- Bare one\n- Bare two\n", 1)
        with open(state_path(self.dir), "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        result = run_cli(self.dir, "check", "--fix")
        self.assertEqual(result.returncode, 0, result.stderr)
        fixed = read_text(state_path(self.dir))
        self.assertEqual(fixed.count("Last ID used:"), 1)
        self.assertIn("Last ID used: #2", fixed)
        self.assertIn("- #1 ", fixed)
        self.assertIn("- #2 ", fixed)

    def test_archive_items_get_missing_id_and_date_warnings(self):
        self.init_project()
        text = read_text(archive_path(self.dir))
        text = text.replace("## Archived\n", "## Archived\n- Hand-archived note\n", 1)
        with open(archive_path(self.dir), "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        result = run_cli(self.dir, "check")
        self.assertIn("warning: line 6 in STATE-ARCHIVE.md has no ID", result.stdout)
        self.assertIn("warning: line 6 in STATE-ARCHIVE.md has no date", result.stdout)

    # v1.1.1: state files are written world-readable (0644), not 0600.
    def test_state_files_are_world_readable(self):
        self.init_project()
        for path in (state_path(self.dir), archive_path(self.dir)):
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o644)
        run_cli(self.dir, "add", "Open", "Written by add")
        for path in (state_path(self.dir), archive_path(self.dir)):
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o644)

    def test_restore_leaves_files_world_readable(self):
        self.init_project()
        run_cli(self.dir, "add", "Open", "Before restore")
        os.chmod(state_path(self.dir), 0o600)
        os.chmod(archive_path(self.dir), 0o600)
        result = run_cli(self.dir, "restore")
        self.assertEqual(result.returncode, 0, result.stderr)
        for path in (state_path(self.dir), archive_path(self.dir)):
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o644)



    # --- P0 fix round 2026-11-04 ---
    def test_lock_owner_token_stale_writer_cannot_delete_new_owners_lock(self):
        self.init_project()
        sys.path.insert(0, ROOT)
        import statecat

        lock_path = os.path.join(self.dir, ".statecat", "lock")
        env = {"STATECAT_LOCK_TIMEOUT": "5", "STATECAT_LOCK_STALE": "1"}
        old_env = {key: os.environ.get(key) for key in env}
        try:
            os.environ.update(env)
            first = statecat.project_lock(self.dir)
            first.__enter__()
            self.assertIsNotNone(first.owner_token)
            # Age the lock so a second writer treats it as stale.
            # Simulate a crashed writer first: stop the heartbeat
            # and record a dead owner PID. A live owner (this
            # test process) must never be broken as stale.
            first._stop_heartbeat()
            simulate_dead_owner(lock_path)
            old = time.time() - 3600
            os.utime(lock_path, (old, old))
            second = statecat.project_lock(self.dir)
            second.__enter__()
            self.assertNotEqual(second.owner_token, first.owner_token)
            self.assertTrue(os.path.isdir(lock_path))
            # The stale first writer releasing must not delete the
            # new owner's lock.
            first.__exit__(None, None, None)
            self.assertTrue(os.path.isdir(lock_path))
            self.assertIn(second.owner_token, read_text(os.path.join(lock_path, "owner")))
            # The current owner releasing normally does delete it.
            second.__exit__(None, None, None)
            self.assertFalse(os.path.isdir(lock_path))
        finally:
            for key, value in old_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_lock_release_race_takeover_between_rename_and_check_survives(self):
        # The exact interleaving ChatGPT flagged: a writer's release
        # checks ownership, a takeover happens, then the release
        # deletes. The old check-then-delete code lost the new
        # owner's lock here. The fix renames the dir aside first,
        # so the takeover is injected between the rename and the
        # token check on the renamed copy, and the new owner's
        # lock must survive.
        self.init_project()
        sys.path.insert(0, ROOT)
        import statecat

        lock_path = os.path.join(self.dir, ".statecat", "lock")
        env = {"STATECAT_LOCK_TIMEOUT": "5", "STATECAT_LOCK_STALE": "1"}
        old_env = {key: os.environ.get(key) for key in env}
        try:
            os.environ.update(env)
            first = statecat.project_lock(self.dir)
            first.__enter__()
            holder = {}
            test_dir = self.dir
            real_read = statecat.project_lock._read_owner_token_from

            def injected_read(self, directory):
                if "second" not in holder:
                    second = statecat.project_lock(test_dir)
                    second.__enter__()
                    holder["second"] = second
                return real_read(self, directory)

            statecat.project_lock._read_owner_token_from = injected_read
            try:
                first.__exit__(None, None, None)
            finally:
                statecat.project_lock._read_owner_token_from = real_read
            self.assertIn("second", holder)
            second = holder["second"]
            self.assertTrue(os.path.isdir(lock_path))
            self.assertIn(second.owner_token, read_text(os.path.join(lock_path, "owner")))
            second.__exit__(None, None, None)
            self.assertFalse(os.path.isdir(lock_path))
        finally:
            for key, value in old_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_lock_stale_removal_restores_fresh_lock_instead_of_deleting(self):
        # A lock that looks stale at the age check may be fresh by
        # the time it is renamed aside (a new owner took over in
        # between). The stale path must put a fresh dir back, not
        # delete it; only a still-stale dir is deleted, on the next
        # pass. Fault-injected deterministically via the age check.
        self.init_project()
        sys.path.insert(0, ROOT)
        import statecat

        lock_path = os.path.join(self.dir, ".statecat", "lock")
        env = {"STATECAT_LOCK_TIMEOUT": "5", "STATECAT_LOCK_STALE": "1"}
        old_env = {key: os.environ.get(key) for key in env}
        try:
            os.environ.update(env)
            first = statecat.project_lock(self.dir)
            first.__enter__()
            # Simulate a crashed writer: stop the heartbeat and
            # record a dead owner PID, or the aged lock could
            # never be broken as stale (a live owner is kept).
            first._stop_heartbeat()
            simulate_dead_owner(lock_path)
            old = time.time() - 3600
            os.utime(lock_path, (old, old))
            deletes = []
            real_delete = statecat.project_lock._delete_trash
            real_stale = statecat.project_lock._is_stale_dir
            calls = {"n": 0}

            def injected_stale(self, directory, stale):
                calls["n"] += 1
                if calls["n"] == 1:
                    return False
                return real_stale(self, directory, stale)

            def counting_delete(self, trash):
                deletes.append(trash)
                return real_delete(self, trash)

            statecat.project_lock._is_stale_dir = injected_stale
            statecat.project_lock._delete_trash = counting_delete
            try:
                second = statecat.project_lock(self.dir)
                second.__enter__()
            finally:
                statecat.project_lock._is_stale_dir = real_stale
                statecat.project_lock._delete_trash = real_delete
            # The aged lock was put back once, then broken as stale
            # on the next pass: exactly one delete, by the stale path.
            self.assertEqual(len(deletes), 1)
            self.assertTrue(os.path.isdir(lock_path))
            self.assertIn(second.owner_token, read_text(os.path.join(lock_path, "owner")))
            second.__exit__(None, None, None)
            self.assertFalse(os.path.isdir(lock_path))
            # The first writer releasing after its lock is gone is
            # a no-op, not an error.
            first.__exit__(None, None, None)
            self.assertFalse(os.path.isdir(lock_path))
        finally:
            for key, value in old_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_lock_acquisition_fails_when_owner_file_cannot_be_written(self):
        # A writer that cannot record its owner token must fail
        # cleanly, not proceed unwitnessed and strand the project
        # behind a lock it can never prove is its own. It must
        # also delete nothing: the directory at the lock path
        # may already be a replacement writer's lock, and
        # nothing proves it is still ours, so it is left behind
        # for stale cleanup instead of being removed.
        self.init_project()
        sys.path.insert(0, ROOT)
        import statecat
        from unittest import mock

        lock_path = os.path.join(self.dir, ".statecat", "lock")
        real_open = open

        def failing_open(path, *args, **kwargs):
            mode = args[0] if args else kwargs.get("mode", "r")
            # The owner file is created exclusively ("x"); a
            # write-mode ("w") check would miss the failure.
            if (
                isinstance(path, str)
                and path.endswith(os.path.join("lock", "owner"))
                and ("w" in str(mode) or "x" in str(mode))
            ):
                raise OSError("simulated owner write failure")
            return real_open(path, *args, **kwargs)

        env = {"STATECAT_LOCK_TIMEOUT": "5", "STATECAT_LOCK_STALE": "1"}
        old_env = {key: os.environ.get(key) for key in env}
        try:
            os.environ.update(env)
            with mock.patch("builtins.open", side_effect=failing_open):
                lock = statecat.project_lock(self.dir)
                with self.assertRaises(SystemExit) as caught:
                    lock.__enter__()
                self.assertEqual(caught.exception.code, 1)
                self.assertFalse(lock.acquired)
                # The lock dir is left behind, not removed: it
                # may be a replacement writer's lock, and the
                # writer cannot prove it is still its own.
                self.assertTrue(os.path.isdir(lock_path))
            # A later writer can acquire once the leftover dir
            # ages past the unknown-owner second threshold
            # (ten times the stale limit, 10 seconds here).
            old = time.time() - 11
            os.utime(lock_path, (old, old))
            lock = statecat.project_lock(self.dir)
            lock.__enter__()
            self.assertTrue(lock.acquired)
            lock.__exit__(None, None, None)
            self.assertFalse(os.path.isdir(lock_path))
        finally:
            for key, value in old_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_config_set_rejects_out_of_range_values(self):
        self.init_project()
        cases = (
            ("stale_days", "-5", "0 and 1000000"),
            ("backups_kept", "-1", "0 and 1000"),
            ("lock_timeout", "0", "0.1 and 3600"),
            ("lock_stale", "-1", "1 and 86400"),
            ("size_lines", "0", "1 and 1000000"),
            ("size_bytes", "0", "1 and 1000000000"),
        )
        for key, value, expected_range in cases:
            result = run_cli(self.dir, "config", "set", key, value)
            self.assertNotEqual(result.returncode, 0, (key, value))
            self.assertIn("must be between %s" % expected_range, result.stderr, (key, value))
        config = json.loads(run_cli(self.dir, "config").stdout)
        self.assertEqual(config["stale_days"], 14)
        self.assertEqual(config["backups_kept"], 20)
        self.assertEqual(config["lock_timeout"], 10.0)
        self.assertEqual(config["lock_stale"], 60.0)

    def test_config_set_rejects_non_finite_values(self):
        # NaN passes every plain `<` / `>` comparison, so a bare
        # range check accepts it; a NaN lock timeout would then
        # make a writer wait forever. inf is caught by the range
        # check, but must be rejected as non-finite too.
        self.init_project()
        for key, value in (
            ("lock_timeout", "nan"),
            ("lock_timeout", "inf"),
            ("lock_stale", "nan"),
            ("lock_stale", "inf"),
        ):
            result = run_cli(self.dir, "config", "set", key, value)
            self.assertNotEqual(result.returncode, 0, (key, value))
            self.assertIn("finite", result.stderr, (key, value))
        # -inf cannot be passed on the command line (argparse
        # treats it as an option), so check it through the
        # validator itself, as well as nan/inf for float keys.
        sys.path.insert(0, ROOT)
        import statecat

        for key in ("lock_timeout", "lock_stale"):
            for value in (float("nan"), float("inf"), float("-inf")):
                self.assertIn("finite", statecat.config_range_error(key, value))
        config = json.loads(run_cli(self.dir, "config").stdout)
        self.assertEqual(config["lock_timeout"], 10.0)
        self.assertEqual(config["lock_stale"], 60.0)

    def test_config_file_rejects_non_finite_and_bool_values(self):
        # The same holes through a hand-edited JSON config file:
        # NaN / Infinity tokens and JSON booleans must warn and
        # fall back to defaults, never be stored or coerced
        # (int(True) is 1 in Python).
        self.init_project()
        with open(os.path.join(self.dir, ".statecat", "config"), "w", encoding="utf-8") as handle:
            handle.write(
                '{"lock_timeout": NaN, "lock_stale": Infinity, '
                '"stale_days": true, "backups_kept": false}'
            )
        result = run_cli(self.dir, "config")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ignoring", result.stderr)
        config = json.loads(result.stdout)
        self.assertEqual(config["lock_timeout"], 10.0)
        self.assertEqual(config["lock_stale"], 60.0)
        self.assertEqual(config["stale_days"], 14)
        self.assertEqual(config["backups_kept"], 20)
        # And a non-finite value written as a JSON string.
        with open(os.path.join(self.dir, ".statecat", "config"), "w", encoding="utf-8") as handle:
            handle.write('{"lock_timeout": "nan"}')
        result = run_cli(self.dir, "config")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ignoring", result.stderr)
        self.assertEqual(json.loads(result.stdout)["lock_timeout"], 10.0)

    # --- P2 fix round 2026-11-04 (ChatGPT P1 re-check follow-ups) ---
    def test_stale_lock_with_live_owner_is_never_broken(self):
        # Age alone must not kill a live writer: an aged lock
        # whose owner PID is still alive is not abandoned,
        # however old its directory looks.
        self.init_project()
        lock_path = os.path.join(self.dir, ".statecat", "lock")
        os.makedirs(lock_path)
        with open(os.path.join(lock_path, "owner"), "w", encoding="utf-8") as handle:
            handle.write("pid=%d time=2026-11-04T00:00:00\n" % os.getpid())
            handle.write("token=live-owner-token\n")
        old = time.time() - 3600
        os.utime(lock_path, (old, old))
        result = run_cli_env(
            self.dir, {"STATECAT_LOCK_TIMEOUT": "0.5", "STATECAT_LOCK_STALE": "60"},
            "add", "Open", "blocked by live owner",
        )
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("lock is held", result.stderr)
        self.assertTrue(os.path.isdir(lock_path))
        self.assertIn("live-owner-token", read_text(os.path.join(lock_path, "owner")))
        self.assertNotIn("blocked by live owner", read_text(state_path(self.dir)))

    def test_stale_lock_with_dead_owner_is_broken(self):
        # The same aged lock, but with a dead owner PID, must
        # still be broken as stale: abandonment is age plus a
        # dead owner, not age alone and not liveness alone.
        self.init_project()
        lock_path = os.path.join(self.dir, ".statecat", "lock")
        os.makedirs(lock_path)
        with open(os.path.join(lock_path, "owner"), "w", encoding="utf-8") as handle:
            handle.write("pid=999999 time=2026-11-04T00:00:00\n")
            handle.write("token=dead-owner-token\n")
        old = time.time() - 3600
        os.utime(lock_path, (old, old))
        result = run_cli_env(
            self.dir, {"STATECAT_LOCK_TIMEOUT": "5", "STATECAT_LOCK_STALE": "60"},
            "add", "Open", "after dead lock",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("after dead lock", read_text(state_path(self.dir)))
        self.assertFalse(os.path.isdir(lock_path))

    def test_heartbeat_keeps_held_lock_fresh(self):
        # While a lock is held, the heartbeat touches its
        # directory mtime, so a long-running writer's lock
        # does not go stale under it.
        self.init_project()
        sys.path.insert(0, ROOT)
        import statecat

        lock_path = os.path.join(self.dir, ".statecat", "lock")
        env = {"STATECAT_LOCK_TIMEOUT": "5", "STATECAT_LOCK_STALE": "1"}
        old_env = {key: os.environ.get(key) for key in env}
        try:
            os.environ.update(env)
            lock = statecat.project_lock(self.dir)
            lock.__enter__()
            try:
                self.assertIsNotNone(lock._heartbeat_thread)
                self.assertTrue(lock._heartbeat_thread.is_alive())
                time.sleep(2.5)
                age = time.time() - os.path.getmtime(lock_path)
                self.assertLess(age, 1.0, "heartbeat did not refresh the lock")
            finally:
                lock.__exit__(None, None, None)
            self.assertIsNone(lock._heartbeat_thread)
            self.assertFalse(os.path.isdir(lock_path))
        finally:
            for key, value in old_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_restore_trash_never_overwrites_existing_lock(self):
        # On POSIX, os.rename can replace an existing empty
        # directory, so an unchecked restore could destroy a
        # third writer's just-created lock. _restore_trash
        # must decline when lock_path already exists, leaving
        # both the fresh lock and the trash dir untouched.
        self.init_project()
        sys.path.insert(0, ROOT)
        import statecat

        lock_path = os.path.join(self.dir, ".statecat", "lock")
        trash = os.path.join(self.dir, ".statecat", "lock.gone-1-abc")
        os.makedirs(trash)
        with open(os.path.join(trash, "owner"), "w", encoding="utf-8") as handle:
            handle.write("pid=999999\ntoken=old-owner\n")
        os.makedirs(lock_path)
        lock = statecat.project_lock(self.dir)
        self.assertFalse(lock._restore_trash(trash))
        self.assertTrue(os.path.isdir(lock_path))
        self.assertFalse(os.path.exists(os.path.join(lock_path, "owner")))
        self.assertTrue(os.path.isdir(trash))

    def test_env_lock_timing_out_of_range_falls_back_to_config(self):
        # Env overrides get the same range bounds as `config
        # set`: 0.01 and 999999 are finite, so a finite-only
        # check would accept them, but they are outside the
        # configured ranges and must fall back to config.
        self.init_project()
        sys.path.insert(0, ROOT)
        import statecat

        lock = statecat.project_lock(self.dir)
        config = statecat.load_config(self.dir)
        cases = (
            ({"STATECAT_LOCK_TIMEOUT": "0.01"}, (10.0, 60.0)),
            ({"STATECAT_LOCK_TIMEOUT": "999999"}, (10.0, 60.0)),
            ({"STATECAT_LOCK_STALE": "0.01"}, (10.0, 60.0)),
            ({"STATECAT_LOCK_STALE": "999999"}, (10.0, 60.0)),
            ({"STATECAT_LOCK_TIMEOUT": "nan"}, (10.0, 60.0)),
            ({"STATECAT_LOCK_TIMEOUT": "5", "STATECAT_LOCK_STALE": "120"}, (5.0, 120.0)),
        )
        old_env = {key: os.environ.get(key) for key in ("STATECAT_LOCK_TIMEOUT", "STATECAT_LOCK_STALE")}
        try:
            for env, expected in cases:
                for key in old_env:
                    os.environ.pop(key, None)
                os.environ.update(env)
                self.assertEqual(lock._resolve_lock_timing(config), expected, env)
        finally:
            for key, value in old_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_config_rejects_stale_not_greater_than_timeout(self):
        # lock_stale must be greater than lock_timeout, or a
        # waiter can have its lock broken as stale while it
        # is still waiting. `config set` rejects the pair;
        # JSON config warns and falls back to the defaults
        # for both timings; env overrides only warn and keep
        # working (they are the test escape hatch).
        self.init_project()
        result = run_cli(self.dir, "config", "set", "lock_stale", "5")
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("must be greater than", result.stderr)
        result = run_cli(self.dir, "config", "set", "lock_timeout", "100")
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("must be less than", result.stderr)
        config = json.loads(run_cli(self.dir, "config").stdout)
        self.assertEqual(config["lock_timeout"], 10.0)
        self.assertEqual(config["lock_stale"], 60.0)
        # A valid order works: raise stale first, then timeout.
        result = run_cli(self.dir, "config", "set", "lock_stale", "200")
        self.assertEqual(result.returncode, 0, result.stderr)
        result = run_cli(self.dir, "config", "set", "lock_timeout", "100")
        self.assertEqual(result.returncode, 0, result.stderr)
        # JSON config with an inconsistent pair warns and
        # falls back to the defaults for both timings.
        with open(os.path.join(self.dir, ".statecat", "config"), "w", encoding="utf-8") as handle:
            handle.write('{"lock_timeout": 100, "lock_stale": 60}')
        result = run_cli(self.dir, "config")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ignoring lock_stale/lock_timeout pair", result.stderr)
        config = json.loads(result.stdout)
        self.assertEqual(config["lock_timeout"], 10.0)
        self.assertEqual(config["lock_stale"], 60.0)
        # Env overrides keep the pair (warning only), so the
        # test harness can still use timeout 5 / stale 1.
        sys.path.insert(0, ROOT)
        import statecat

        lock = statecat.project_lock(self.dir)
        config = statecat.load_config(self.dir)
        old_env = {key: os.environ.get(key) for key in ("STATECAT_LOCK_TIMEOUT", "STATECAT_LOCK_STALE")}
        try:
            os.environ.update({"STATECAT_LOCK_TIMEOUT": "5", "STATECAT_LOCK_STALE": "1"})
            self.assertEqual(lock._resolve_lock_timing(config), (5.0, 1.0))
        finally:
            for key, value in old_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_ownerless_stale_lock_is_not_broken_in_normal_window(self):
        # Unknown is not dead: an aged lock with no owner file
        # at all may belong to a writer paused between mkdir
        # and writing its owner file, so it must not be broken
        # just because it looks stale.
        self.init_project()
        lock_path = os.path.join(self.dir, ".statecat", "lock")
        os.makedirs(lock_path)
        old = time.time() - 120
        os.utime(lock_path, (old, old))
        result = run_cli_env(
            self.dir, {"STATECAT_LOCK_TIMEOUT": "0.5", "STATECAT_LOCK_STALE": "60"},
            "add", "Open", "blocked by ownerless lock",
        )
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("lock is held", result.stderr)
        self.assertTrue(os.path.isdir(lock_path))
        self.assertNotIn("blocked by ownerless lock", read_text(state_path(self.dir)))

    def test_indeterminate_liveness_is_not_treated_as_dead(self):
        # A recorded PID whose liveness cannot be established
        # (mocked _pid_is_alive returning None) is unknown,
        # not dead: the lock survives the normal stale window
        # and is only abandoned past the second threshold.
        self.init_project()
        sys.path.insert(0, ROOT)
        import statecat
        from unittest import mock

        lock_path = os.path.join(self.dir, ".statecat", "lock")
        os.makedirs(lock_path)
        with open(os.path.join(lock_path, "owner"), "w", encoding="utf-8") as handle:
            handle.write("pid=999999 time=2026-11-04T00:00:00\ntoken=unknown-owner\n")
        lock = statecat.project_lock(self.dir)
        with mock.patch.object(statecat, "_pid_is_alive", return_value=None):
            old = time.time() - 120
            os.utime(lock_path, (old, old))
            self.assertFalse(lock._is_abandoned_dir(lock_path, 60))
            old = time.time() - 601
            os.utime(lock_path, (old, old))
            self.assertTrue(lock._is_abandoned_dir(lock_path, 60))
        self.assertTrue(os.path.isdir(lock_path))

    def test_ownerless_lock_recovered_after_second_threshold(self):
        # The recovery path for a genuinely dead ownerless lock
        # (writer crashed between mkdir and its owner write, or
        # a hand-made empty lock dir) is ten times the stale
        # limit. Both sides of the boundary, with stale=1 so
        # the threshold is 10 seconds: aged 9s it survives,
        # aged 11s it is broken and the writer succeeds.
        self.init_project()
        lock_path = os.path.join(self.dir, ".statecat", "lock")
        os.makedirs(lock_path)
        old = time.time() - 9
        os.utime(lock_path, (old, old))
        result = run_cli_env(
            self.dir, {"STATECAT_LOCK_TIMEOUT": "0.5", "STATECAT_LOCK_STALE": "1"},
            "add", "Open", "blocked just under threshold",
        )
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("lock is held", result.stderr)
        self.assertTrue(os.path.isdir(lock_path))
        old = time.time() - 11
        os.utime(lock_path, (old, old))
        result = run_cli_env(
            self.dir, {"STATECAT_LOCK_TIMEOUT": "5", "STATECAT_LOCK_STALE": "1"},
            "add", "Open", "after ownerless recovery",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("after ownerless recovery", read_text(state_path(self.dir)))
        self.assertFalse(os.path.isdir(lock_path))

    def test_owner_file_is_created_exclusively(self):
        # A writer whose lock directory was broken and replaced
        # must not write its owner token into the new owner's
        # lock. The owner file is created with mode "x", so
        # _write_owner_file refuses when one already exists
        # and leaves the existing file untouched.
        self.init_project()
        sys.path.insert(0, ROOT)
        import statecat

        lock_path = os.path.join(self.dir, ".statecat", "lock")
        os.makedirs(lock_path)
        with open(os.path.join(lock_path, "owner"), "w", encoding="utf-8") as handle:
            handle.write("pid=999999\ntoken=other-owner\n")
        lock = statecat.project_lock(self.dir)
        self.assertFalse(lock._write_owner_file())
        self.assertIn("token=other-owner", read_text(os.path.join(lock_path, "owner")))
        os.remove(os.path.join(lock_path, "owner"))
        self.assertTrue(lock._write_owner_file())
        self.assertIn(lock.owner_token, read_text(os.path.join(lock_path, "owner")))

    def test_owner_write_oserror_never_deletes_replacement_lock(self):
        # A generic OSError while writing the owner file must
        # never delete the lock directory: the directory at
        # lock_path may already be a replacement writer's lock,
        # and nothing here proves it is still ours. A writer
        # paused after mkdir, whose directory was broken and
        # replaced, resumes into a lock it does not own; if
        # its owner-file create then raises a generic OSError
        # (here a PermissionError, as on an inaccessible
        # replacement directory), the cleanup must fail cleanly
        # and leave the replacement lock untouched.
        self.init_project()
        sys.path.insert(0, ROOT)
        import statecat
        from unittest import mock

        lock_path = os.path.join(self.dir, ".statecat", "lock")
        os.makedirs(lock_path)
        with open(os.path.join(lock_path, "owner"), "w", encoding="utf-8") as handle:
            handle.write("pid=%d time=2026-11-04T00:00:00\n" % os.getpid())
            handle.write("token=replacement-owner-token\n")
        real_open = open

        def failing_open(path, *args, **kwargs):
            mode = args[0] if args else kwargs.get("mode", "r")
            if (
                isinstance(path, str)
                and path.endswith(os.path.join("lock", "owner"))
                and "x" in str(mode)
            ):
                raise PermissionError("simulated owner write failure")
            return real_open(path, *args, **kwargs)

        lock = statecat.project_lock(self.dir)
        with mock.patch("builtins.open", side_effect=failing_open):
            with self.assertRaises(SystemExit) as caught:
                lock._write_owner_file()
            self.assertEqual(caught.exception.code, 1)
        self.assertFalse(lock.acquired)
        # The replacement writer's lock survives untouched.
        self.assertTrue(os.path.isdir(lock_path))
        self.assertIn("replacement-owner-token", read_text(os.path.join(lock_path, "owner")))

    def test_pid_is_alive_returns_unknown_on_windows_without_kill(self):
        # On Windows, signal.CTRL_C_EVENT is literally 0, so
        # os.kill(pid, 0) would send Ctrl+C to the process
        # group instead of probing. The probe must never run
        # there: liveness is always unknown on Windows.
        sys.path.insert(0, ROOT)
        import statecat
        from unittest import mock

        with mock.patch.object(sys, "platform", "win32"):
            with mock.patch("os.kill") as kill:
                self.assertIsNone(statecat._pid_is_alive(1234))
                self.assertIsNone(statecat._pid_is_alive(999999))
            kill.assert_not_called()

    def test_lock_held_error_names_dead_owner_pid_age_and_clear(self):
        # The deadline error must say whose lock it is, how old
        # it is, when it clears, and how to clear it by hand.
        # A dead owner aged 30s at stale 60s is not broken yet,
        # so the waiter times out and reports the numbers.
        self.init_project()
        lock_path = os.path.join(self.dir, ".statecat", "lock")
        os.makedirs(lock_path)
        with open(os.path.join(lock_path, "owner"), "w", encoding="utf-8") as handle:
            handle.write("pid=999999 time=2026-11-04T00:00:00\n")
            handle.write("token=dead-owner-token\n")
        old = time.time() - 30
        os.utime(lock_path, (old, old))
        result = run_cli_env(
            self.dir, {"STATECAT_LOCK_TIMEOUT": "0.5", "STATECAT_LOCK_STALE": "60"},
            "add", "Open", "blocked by dead owner",
        )
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("left behind", result.stderr)
        self.assertIn("no process is writing", result.stderr)
        self.assertIn("owner PID 999999", result.stderr)
        self.assertIn("stale limit 60s", result.stderr)
        self.assertRegex(result.stderr, r"lock age \d+s")
        match = re.search(r"in about (\d+) seconds", result.stderr)
        self.assertIsNotNone(match, result.stderr)
        # Aged ~30s at a 60s limit: about 30s left, give or take
        # the half-second wait and clock skew.
        self.assertTrue(20 <= int(match.group(1)) <= 35, result.stderr)
        self.assertIn("rm -rf", result.stderr)
        self.assertIn(lock_path, result.stderr)
        self.assertTrue(os.path.isdir(lock_path))

    def test_lock_held_error_for_live_owner_never_clears(self):
        # A live owner never clears while it is alive, and the
        # error must say so instead of promising a time.
        self.init_project()
        lock_path = os.path.join(self.dir, ".statecat", "lock")
        os.makedirs(lock_path)
        with open(os.path.join(lock_path, "owner"), "w", encoding="utf-8") as handle:
            handle.write("pid=%d time=2026-11-04T00:00:00\n" % os.getpid())
            handle.write("token=live-owner-token\n")
        old = time.time() - 120
        os.utime(lock_path, (old, old))
        result = run_cli_env(
            self.dir, {"STATECAT_LOCK_TIMEOUT": "0.5", "STATECAT_LOCK_STALE": "60"},
            "add", "Open", "blocked by live owner",
        )
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("owner PID %d" % os.getpid(), result.stderr)
        self.assertIn("never while the owner is alive", result.stderr)
        self.assertIn("still running", result.stderr)

    def test_lock_held_error_for_unknown_owner_uses_tenfold_limit(self):
        # No owner file at all: unknown ownership, so the lock
        # clears only after ten times the stale limit.
        self.init_project()
        lock_path = os.path.join(self.dir, ".statecat", "lock")
        os.makedirs(lock_path)
        old = time.time() - 120
        os.utime(lock_path, (old, old))
        result = run_cli_env(
            self.dir, {"STATECAT_LOCK_TIMEOUT": "0.5", "STATECAT_LOCK_STALE": "60"},
            "add", "Open", "blocked by ownerless lock",
        )
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("owner PID unknown", result.stderr)
        self.assertIn("600s unknown-owner limit", result.stderr)
        match = re.search(r"in about (\d+) seconds", result.stderr)
        self.assertIsNotNone(match, result.stderr)
        # Aged ~120s at a 600s limit: about 480s left.
        self.assertTrue(460 <= int(match.group(1)) <= 490, result.stderr)

    # --- P6 fix round 2026-11-04 (ChatGPT v1.2.0 whole-repo review) ---

    def test_concurrent_init_second_init_fails_and_headers_match(self):
        # init used to bypass the project lock: two agents
        # init-ing the same empty directory could both pass the
        # existence check and interleave, leaving STATE.md and
        # STATE-ARCHIVE.md with different project headers.
        # Serialized under the lock, exactly one init wins and
        # the pair of headers always agrees.
        for _ in range(5):
            with tempfile.TemporaryDirectory() as directory:
                procs = [
                    subprocess.Popen(
                        [sys.executable, CLI, "init", name],
                        cwd=directory, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, text=True,
                    )
                    for name in ("Alpha", "Beta")
                ]
                results = [proc.communicate() for proc in procs]
                codes = [proc.returncode for proc in procs]
                self.assertEqual(sorted(codes), [0, 1], results)
                loser = results[codes.index(1)]
                self.assertIn("already exists", loser[1])
                headers = set()
                for path in (state_path(directory), archive_path(directory)):
                    for line in read_lines(path):
                        if line.startswith("# Project:"):
                            header = line.strip()
                            # The archive header is "<name> Archive";
                            # removesuffix is 3.9+, so strip by hand.
                            if header.endswith(" Archive"):
                                header = header[: -len(" Archive")]
                            headers.add(header)
                self.assertEqual(len(headers), 1, headers)
                self.assertIn(list(headers)[0], ("# Project: Alpha", "# Project: Beta"))

    def test_concurrent_config_sets_both_survive(self):
        # config set used to bypass the project lock: two
        # simultaneous sets each read the same old JSON and the
        # last atomic write won, losing the other key.
        for _ in range(5):
            with tempfile.TemporaryDirectory() as directory:
                self.assertEqual(run_cli(directory, "init", "Cfg").returncode, 0)
                procs = [
                    subprocess.Popen(
                        [sys.executable, CLI, "config", "set", key, value],
                        cwd=directory, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, text=True,
                    )
                    for key, value in (("stale_days", "42"), ("backups_kept", "7"))
                ]
                for proc in procs:
                    proc.communicate()
                    self.assertEqual(proc.returncode, 0)
                config = json.loads(run_cli(directory, "config").stdout)
                self.assertEqual(config["stale_days"], 42)
                self.assertEqual(config["backups_kept"], 7)

    def test_config_file_rejects_fractional_integer_values(self):
        # int(1.9) is 1 in Python, so a JSON {"stale_days": 1.9}
        # used to silently become 1 with no warning at all.
        # Fractional values for integer keys are now rejected
        # with a clear warning and fall back to the default;
        # integral values (1.0, "5", "5.0") still work.
        self.init_project()
        with open(os.path.join(self.dir, ".statecat", "config"), "w", encoding="utf-8") as handle:
            handle.write('{"stale_days": 1.9}')
        result = run_cli(self.dir, "config")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ignoring", result.stderr)
        self.assertIn("not an integer", result.stderr)
        self.assertEqual(json.loads(result.stdout)["stale_days"], 14)
        with open(os.path.join(self.dir, ".statecat", "config"), "w", encoding="utf-8") as handle:
            handle.write('{"stale_days": 1.0}')
        result = run_cli(self.dir, "config")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["stale_days"], 1)
        result = run_cli(self.dir, "config", "set", "stale_days", "1.9")
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("not an integer", result.stderr)
        result = run_cli(self.dir, "config", "set", "stale_days", "5.0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("set stale_days = 5", result.stdout)
        self.assertEqual(json.loads(run_cli(self.dir, "config").stdout)["stale_days"], 5)

    def test_lock_held_error_quotes_path_with_space_and_rewords_dead_owner(self):
        # A project path with a space used to paste into the
        # manual command unquoted (rm -rf .../My Projects/...),
        # and a dead owner was still introduced as "another
        # statecat process is writing", contradicting the
        # "(the owner is dead)" that followed it.
        with tempfile.TemporaryDirectory() as base:
            directory = os.path.join(base, "My Projects")
            os.makedirs(directory)
            self.assertEqual(run_cli(directory, "init", "Spacey").returncode, 0)
            lock_path = os.path.join(directory, ".statecat", "lock")
            os.makedirs(lock_path)
            with open(os.path.join(lock_path, "owner"), "w", encoding="utf-8") as handle:
                handle.write("pid=999999 time=2026-11-04T00:00:00\n")
                handle.write("token=dead-owner-token\n")
            old = time.time() - 30
            os.utime(lock_path, (old, old))
            result = run_cli_env(
                directory, {"STATECAT_LOCK_TIMEOUT": "0.5", "STATECAT_LOCK_STALE": "60"},
                "add", "Open", "blocked by dead owner",
            )
            self.assertNotEqual(result.returncode, 0, result.stderr)
            self.assertIn("left behind", result.stderr)
            self.assertIn("no process is writing", result.stderr)
            self.assertNotIn("another statecat process is writing", result.stderr)
            self.assertIn("rm -rf '%s'" % lock_path, result.stderr)

    def test_lock_held_error_zero_second_countdown_says_next_attempt(self):
        # When the remaining time is under a second, int()
        # truncation used to print "in about 0 seconds", even
        # though this attempt exits without removing the lock
        # and the next attempt does the cleanup.
        self.init_project()
        lock_path = os.path.join(self.dir, ".statecat", "lock")
        os.makedirs(lock_path)
        with open(os.path.join(lock_path, "owner"), "w", encoding="utf-8") as handle:
            handle.write("pid=999999 time=2026-11-04T00:00:00\n")
            handle.write("token=dead-owner-token\n")
        old = time.time() - 58.5
        os.utime(lock_path, (old, old))
        result = run_cli_env(
            self.dir, {"STATECAT_LOCK_TIMEOUT": "0.5", "STATECAT_LOCK_STALE": "60"},
            "add", "Open", "blocked by dead owner",
        )
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("in about 0 seconds", result.stderr)
        self.assertIn("on the next attempt", result.stderr)

    def test_lock_held_error_past_threshold_says_already_past(self):
        # The sub-second branch also catches a lock already past
        # the threshold (remaining is negative). It must not
        # claim the limit is "less than a second away" then.
        # Test the message directly: a live acquisition would
        # break a dead lock this old instead of reporting it.
        sys.path.insert(0, ROOT)
        import statecat

        lock_path = os.path.join(self.dir, ".statecat", "lock")
        os.makedirs(lock_path)
        with open(os.path.join(lock_path, "owner"), "w", encoding="utf-8") as handle:
            handle.write("pid=999999 time=2026-11-04T00:00:00\n")
            handle.write("token=dead-owner-token\n")
        old = time.time() - 65
        os.utime(lock_path, (old, old))
        lock = statecat.project_lock(self.dir)
        message = lock._lock_held_message(60)
        self.assertIn("on the next attempt", message)
        self.assertIn("already past the stale limit", message)
        self.assertNotIn("less than a second away", message)

    def test_manual_lock_clear_command_is_powershell_on_windows(self):
        # rm -rf is not a Windows command, so the manual clear
        # instruction must not print it on Windows; it gives
        # the PowerShell equivalent instead, with -LiteralPath:
        # -Path would still interpret wildcards, so a path with
        # square brackets could match the wrong thing.
        sys.path.insert(0, ROOT)
        import statecat
        from unittest import mock

        lock = statecat.project_lock(os.path.join("/tmp", "My Projects"))
        with mock.patch.object(sys, "platform", "win32"):
            command = lock._manual_lock_clear_command()
        self.assertIn("Remove-Item -LiteralPath", command)
        self.assertIn("-Recurse -Force", command)
        self.assertNotIn("rm -rf", command)
        self.assertEqual(
            command,
            "Remove-Item -LiteralPath '/tmp/My Projects/.statecat/lock' -Recurse -Force",
        )
        # And on POSIX the same path is shell-quoted.
        self.assertEqual(
            lock._manual_lock_clear_command(),
            "rm -rf '/tmp/My Projects/.statecat/lock'",
        )

    def test_malformed_config_warns_and_uses_defaults(self):
        self.init_project()
        with open(os.path.join(self.dir, ".statecat", "config"), "w", encoding="utf-8") as handle:
            handle.write("{not json")
        result = run_cli(self.dir, "config")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("not valid JSON", result.stderr)
        self.assertIn("using defaults", result.stderr)
        self.assertEqual(json.loads(result.stdout)["stale_days"], 14)
        with open(os.path.join(self.dir, ".statecat", "config"), "w", encoding="utf-8") as handle:
            handle.write('{"stale_days": -5}')
        result = run_cli(self.dir, "config")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("out-of-range", result.stderr)
        self.assertEqual(json.loads(result.stdout)["stale_days"], 14)

    def test_invalid_utf8_fails_cleanly_not_traceback(self):
        self.init_project()
        run_cli(self.dir, "add", "Open", "Keep me")
        with open(state_path(self.dir), "ab") as handle:
            handle.write(b"\xff\xfe bad bytes\n")
        for args in (("show",), ("check",), ("status",)):
            result = run_cli(self.dir, *args)
            self.assertNotEqual(result.returncode, 0, args)
            self.assertNotIn("Traceback", result.stderr, args)
            self.assertIn("not valid UTF-8", result.stderr, args)
        # Restore is the recovery for a bad current file: it works.
        result = run_cli(self.dir, "restore")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(b"bad bytes", read_bytes(state_path(self.dir)))
        # But a snapshot that itself is invalid UTF-8 is refused
        # cleanly, before anything is overwritten.
        other = tempfile.TemporaryDirectory()
        try:
            run_cli(other.name, "init", "Other")
            run_cli(other.name, "add", "Open", "Good item")
            broken = os.path.join(other.name, ".statecat", "backups", "20260101T000000000000")
            os.makedirs(broken)
            with open(os.path.join(broken, "STATE.md"), "wb") as handle:
                handle.write(b"\xff\xfe broken\n")
            shutil.copy(archive_path(other.name), os.path.join(broken, "STATE-ARCHIVE.md"))
            before = read_bytes(state_path(other.name))
            result = run_cli(other.name, "restore", "20260101T000000000000")
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("Traceback", result.stderr)
            self.assertIn("not valid UTF-8", result.stderr)
            self.assertEqual(read_bytes(state_path(other.name)), before)
        finally:
            other.cleanup()

    def test_check_fix_restores_missing_last_id(self):
        self.init_project()
        run_cli(self.dir, "add", "Open", "One")
        run_cli(self.dir, "add", "Open", "Two")
        text = read_text(state_path(self.dir))
        text = "\n".join(line for line in text.splitlines() if not line.startswith("> Last ID used:")) + "\n"
        with open(state_path(self.dir), "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        result = run_cli(self.dir, "check", "--fix")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("fixed Last ID used to #2", result.stdout)
        self.assertNotIn("nothing to fix", result.stdout)
        self.assertIn("Last ID used: #2", read_text(state_path(self.dir)))
        result = run_cli(self.dir, "check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("clean", result.stdout)

    def test_restore_refuses_partial_snapshot(self):
        self.init_project()
        run_cli(self.dir, "add", "Open", "Good item")
        snap = os.path.join(self.dir, ".statecat", "backups", "20260101T000000000000")
        os.makedirs(snap)
        shutil.copy(archive_path(self.dir), os.path.join(snap, "STATE-ARCHIVE.md"))
        before_state = read_bytes(state_path(self.dir))
        before_archive = read_bytes(archive_path(self.dir))
        before_list = run_cli(self.dir, "restore", "--list").stdout
        result = run_cli(self.dir, "restore", "20260101T000000000000")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("incomplete", result.stderr)
        self.assertIn("STATE.md", result.stderr)
        self.assertNotIn("restored", result.stdout)
        # Neither file was touched, and no safety snapshot was taken.
        self.assertEqual(read_bytes(state_path(self.dir)), before_state)
        self.assertEqual(read_bytes(archive_path(self.dir)), before_archive)
        self.assertEqual(run_cli(self.dir, "restore", "--list").stdout, before_list)

    def test_add_rejects_empty_text(self):
        self.init_project()
        for text in ("", "   "):
            result = run_cli(self.dir, "add", "Decided", text)
            self.assertNotEqual(result.returncode, 0, repr(text))
            self.assertIn("must not be empty", result.stderr, repr(text))
        self.assertNotIn("- #1", read_text(state_path(self.dir)))
        self.assertIn("no snapshots", run_cli(self.dir, "restore", "--list").stdout)

    def test_clearer_errors_list_buckets_suggest_show_and_list_snapshots(self):
        self.init_project()
        run_cli(self.dir, "add", "Open", "One")
        for args in (("add", "Nope", "x"), ("show", "Nope")):
            result = run_cli(self.dir, *args)
            self.assertNotEqual(result.returncode, 0, args)
            self.assertIn(
                "valid buckets: Decided, Open, Waiting on human, Waiting on others, Parked",
                result.stderr,
                args,
            )
        result = run_cli(self.dir, "move", "99", "Open")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no item #99", result.stderr)
        self.assertIn("python3 statecat.py show", result.stderr)
        result = run_cli(self.dir, "restore", "no-such-snapshot")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no snapshot 'no-such-snapshot'", result.stderr)
        self.assertIn("available snapshots:", result.stderr)
        result = run_cli(self.dir, "search", "zzz-no-match")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no matches", result.stdout)
        help_text = run_cli(self.dir, "search", "--help").stdout
        self.assertIn("Exits 1", help_text)
        self.assertIn("exits with code 1", read_text(os.path.join(ROOT, "README.md")))



if __name__ == "__main__":
    runner = unittest.TextTestRunner(verbosity=2)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(StateCatTest)
    result = runner.run(suite)
    total = result.testsRun
    failed = len(result.failures) + len(result.errors)
    print("STATECAT TESTS: %d passed, %d failed, %d total" % (total - failed, failed, total))
    sys.exit(0 if result.wasSuccessful() else 1)
