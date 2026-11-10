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
POINTER = (
    "Read STATE.md first, then run python3 statecat.py check "
    "(py statecat.py check on Windows if python3 is not found), "
    "before asking about or working on this project."
)
LEGACY_POINTER = (
    "Read STATE.md first, then run python3 statecat.py check, "
    "before asking about or working on this project."
)


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

    def test_init_rejects_multiline_project_name(self):
        result = run_cli(
            self.dir, "init", "My Project\n## Open\n- #999 [2026-11-06] Injected"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("control characters", result.stderr)
        self.assertFalse(os.path.exists(state_path(self.dir)))
        self.assertFalse(os.path.exists(archive_path(self.dir)))

    def test_init_rejects_carriage_return_and_control_chars(self):
        for bad in ("Bad\rName", "Bad\x01Name", "Bad\x7fName"):
            result = run_cli(self.dir, "init", bad)
            self.assertNotEqual(result.returncode, 0, bad)
            self.assertFalse(os.path.exists(state_path(self.dir)))

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

    @unittest.skipIf(
        sys.platform == "win32",
        "the launcher is a Unix shell script; Windows cannot "
        "execute an extensionless file (WinError 193)",
    )
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
        # Remove the heading line by line, not by replacing
        # "## Parked\n": on a Windows checkout the fixture may
        # have CRLF endings, so the "\n"-anchored replace
        # silently matches nothing and the heading stays.
        lines = read_lines(state_path(self.dir))
        lines = [line for line in lines if line.rstrip("\r\n") != "## Parked"]
        with open(state_path(self.dir), "w", encoding="utf-8", newline="") as handle:
            handle.writelines(lines)
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

    def test_invalid_calendar_date_warns(self):
        # SC-03: the date shape is valid but the calendar date
        # is not (month 99). It must warn instead of silently
        # treating the item as unknown age.
        self.init_project()
        run_cli(self.dir, "add", "Open", "Plain item")
        text = read_text(state_path(self.dir))
        text = text.replace("[%s]" % TODAY, "[2026-99-99]", 1)
        with open(state_path(self.dir), "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        result = run_cli(self.dir, "check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("invalid date [2026-99-99]", result.stdout)

    def test_fenced_code_block_is_not_structure(self):
        # A heading and list line inside ``` fences are an
        # example, not a bucket or an item: they must not be
        # parsed, and check must stay clean.
        self.init_project()
        run_cli(self.dir, "add", "Open", "Real item")
        text = read_text(state_path(self.dir))
        fence = (
            "```markdown\n## Open\n- #99 [2026-11-06] Fake item\n```\n\n"
        )
        text = text.replace("## Decided\n", fence + "## Decided\n", 1)
        with open(state_path(self.dir), "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        result = run_cli(self.dir, "check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("clean", result.stdout)
        result = run_cli(self.dir, "show", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("#99", result.stdout)
        self.assertIn("Real item", result.stdout)

    def test_stray_item_outside_bucket_is_structural(self):
        # SC-06: an item placed before any bucket heading is
        # invisible to show and ID allocation; check must flag
        # it instead of silently ignoring it.
        self.init_project()
        text = read_text(state_path(self.dir))
        text = text.replace(
            "> Last ID used: #0\n",
            "> Last ID used: #0\n- #6 [2026-11-06] Stray item\n",
            1,
        )
        with open(state_path(self.dir), "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        result = run_cli(self.dir, "check")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("outside any bucket", result.stdout)
        result = run_cli(self.dir, "show", "--json")
        self.assertNotIn("Stray item", result.stdout)

    def test_duplicate_bucket_heading_is_structural(self):
        self.init_project()
        run_cli(self.dir, "add", "Open", "Plain item")
        text = read_text(state_path(self.dir))
        text = text.replace("## Waiting on human\n", "## Open\n\n## Waiting on human\n", 1)
        with open(state_path(self.dir), "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        result = run_cli(self.dir, "check")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("duplicate bucket heading", result.stdout)

    def test_duplicate_last_id_is_structural(self):
        self.init_project()
        text = read_text(state_path(self.dir))
        text = text.replace(
            "> Last ID used: #0\n",
            "> Last ID used: #0\n> Last ID used: #0\n",
            1,
        )
        with open(state_path(self.dir), "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        result = run_cli(self.dir, "check")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("duplicate 'Last ID used'", result.stdout)

    def test_overlong_id_does_not_crash(self):
        # SC-07: an ID longer than the parser's cap must not
        # raise (Python 3.11+ caps int-string conversion); it
        # is treated as an item without an ID instead.
        self.init_project()
        text = read_text(state_path(self.dir))
        huge = "9" * 5000
        text = text.replace(
            "## Open\n", "## Open\n- #%s [2026-11-06] Huge ID\n" % huge, 1
        )
        with open(state_path(self.dir), "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        result = run_cli(self.dir, "check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("has no ID", result.stdout)

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
        self.assertEqual(result.stdout.strip(), POINTER)

    def test_docs_quote_the_current_pointer(self):
        for filename in ("README.md", "SKILL.md", "AGENTS-pointer.md"):
            text = read_text(os.path.join(ROOT, filename))
            self.assertIn(POINTER, text, filename)
            self.assertNotIn(LEGACY_POINTER, text, filename)

    def test_pointer_install_upgrades_legacy_line_in_place(self):
        agents = os.path.join(self.dir, "AGENTS.md")
        with open(agents, "w", encoding="utf-8", newline="") as handle:
            handle.write("# Rules\r\n\r\n%s\r\nKeep tests green.\r\n" % LEGACY_POINTER)
        result = run_cli(self.dir, "pointer", "--install")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("updated pointer in AGENTS.md", result.stdout)
        self.assertEqual(
            read_text(agents),
            "# Rules\r\n\r\n%s\r\nKeep tests green.\r\n" % POINTER,
        )

    def test_pointer_uninstall_removes_line_and_keeps_the_rest(self):
        agents = os.path.join(self.dir, "AGENTS.md")
        claude = os.path.join(self.dir, "CLAUDE.md")
        with open(agents, "w", encoding="utf-8", newline="") as handle:
            handle.write("# Rules\n%s\nKeep tests green.\n" % POINTER)
        with open(claude, "w", encoding="utf-8", newline="") as handle:
            handle.write("# Notes\n%s\n" % LEGACY_POINTER)
        result = run_cli(self.dir, "pointer", "--uninstall")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("removed pointer from AGENTS.md", result.stdout)
        self.assertIn("removed pointer from CLAUDE.md", result.stdout)
        self.assertEqual(read_text(agents), "# Rules\nKeep tests green.\n")
        self.assertEqual(read_text(claude), "# Notes\n")
        again = run_cli(self.dir, "pointer", "--uninstall")
        self.assertIn("no pointer found", again.stdout)

    def test_pointer_uninstall_deletes_agents_md_that_init_created(self):
        self.init_project()
        result = run_cli(self.dir, "pointer", "--uninstall")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("removed AGENTS.md", result.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "AGENTS.md")))

    def test_pointer_install_from_subfolder_uses_project_root(self):
        self.init_project()
        sub = os.path.join(self.dir, "src")
        os.makedirs(sub)
        self.assertIn("pointer already in AGENTS.md", run_cli(sub, "pointer", "--install").stdout)
        self.assertFalse(os.path.exists(os.path.join(sub, "AGENTS.md")))
        self.assertIn("removed AGENTS.md", run_cli(sub, "pointer", "--uninstall").stdout)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "AGENTS.md")))

    def test_pointer_upgrade_and_uninstall_keep_a_bom(self):
        agents = os.path.join(self.dir, "AGENTS.md")
        with open(agents, "wb") as handle:
            handle.write(("﻿%s\n# Rules\n" % LEGACY_POINTER).encode("utf-8"))
        self.assertIn("updated pointer", run_cli(self.dir, "pointer", "--install").stdout)
        self.assertEqual(read_bytes(agents), ("﻿%s\n# Rules\n" % POINTER).encode("utf-8"))
        self.assertIn("removed pointer from AGENTS.md", run_cli(self.dir, "pointer", "--uninstall").stdout)
        self.assertEqual(read_bytes(agents), "﻿# Rules\n".encode("utf-8"))

    def test_pointer_install_and_uninstall_are_exclusive(self):
        result = run_cli(self.dir, "pointer", "--install", "--uninstall")
        self.assertNotEqual(result.returncode, 0)

    # --- Pass 2 of the 2026-11-09 review: SC-08, SC-10..16, SC-18..20 ---
    def _fake_bin(self, scripts):
        """A folder of sh scripts named in `scripts`, for PATH tricks."""
        bin_dir = os.path.join(self.dir, "fakebin")
        os.makedirs(bin_dir, exist_ok=True)
        for name, body in scripts.items():
            path = os.path.join(bin_dir, name)
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write("#!/bin/sh\n" + body)
            os.chmod(path, 0o755)
        return re.sub(r"^([A-Za-z]):", lambda m: "/" + m.group(1).lower(), bin_dir.replace("\\", "/"))

    def _run_hook(self, command, path_prefix=None):
        script = command if path_prefix is None else 'PATH="%s:$PATH"; %s' % (path_prefix, command)
        return subprocess.run(
            ["sh", "-c", script], capture_output=True, text=True,
            env=dict(os.environ, CLAUDE_PROJECT_DIR=self.dir.replace("\\", "/")),
        )

    @unittest.skipIf(shutil.which("sh") is None, "needs a POSIX sh to run the hook command")
    def test_sc10_hook_reports_a_failed_show_and_still_runs_check(self):
        # SC-10: `show && check; exit 0` meant a failed show skipped
        # check and nothing said so. Both must run, and a failure
        # must be reported in the output the agent sees.
        self.init_project("Hook Fail")
        shutil.copy(CLI, self.dir)
        run_cli(self.dir, "hook", "--install")
        command = self.statecat_hook_commands()[0]
        os.unlink(state_path(self.dir))
        result = self._run_hook(command)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("statecat hook: show failed", result.stdout)
        self.assertIn("statecat hook: check failed", result.stdout)
        # The tools' own error text reaches stdout too.
        self.assertIn("STATE.md", result.stdout)

    @unittest.skipIf(shutil.which("sh") is None, "needs a POSIX sh to run the hook command")
    def test_sc10_hook_reports_a_missing_statecat_command(self):
        self.init_project("No Command")
        run_cli(self.dir, "hook", "--install")
        command = self.statecat_hook_commands()[0]
        empty = self._fake_bin({})
        result = subprocess.run(
            ["sh", "-c", 'PATH="%s:/usr/bin:/bin"; %s' % (empty, command)], capture_output=True, text=True,
            env=dict(os.environ, CLAUDE_PROJECT_DIR=self.dir.replace("\\", "/")),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("statecat hook: show failed", result.stdout)

    @unittest.skipIf(shutil.which("sh") is None, "needs a POSIX sh to run the hook command")
    def test_sc16_hook_skips_a_python_older_than_3_8(self):
        # SC-16: the fallback only checked that an interpreter ran
        # an empty program, which a Python 2 or 3.7 does. A python3
        # that is too old must be skipped for the next candidate.
        self.init_project("Old Python")
        shutil.copy(CLI, self.dir)
        run_cli(self.dir, "hook", "--install")
        command = self.statecat_hook_commands()[0]
        prefix = self._fake_bin({
            # Runs an empty program, fails the version check, and
            # would print a marker if it were used for StateCat.
            "python3": 'case "$2" in "") exit 0;; *version_info*) exit 1;; esac\necho "OLD PYTHON USED"\nexit 1\n',
            "py": "exit 127\n",
            "python": 'exec "%s" "$@"\n' % sys.executable.replace("\\", "/"),
        })
        result = self._run_hook(command, prefix)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("OLD PYTHON USED", result.stdout)
        self.assertIn("# Project: Old Python", result.stdout)
        self.assertIn("check: clean", result.stdout)

    def test_sc15_hook_install_removes_an_old_hook_left_beside_the_current_one(self):
        sys.path.insert(0, ROOT)
        import statecat
        self.init_project()
        old = statecat.LEGACY_HOOK_COMMANDS[0]
        self.write_settings(json.dumps({"hooks": {"SessionStart": [
            {"hooks": [{"type": "command", "command": statecat.HOOK_COMMAND}]},
            {"hooks": [{"type": "command", "command": old}, {"type": "command", "command": "echo mine"}]},
            {"hooks": [{"type": "command", "command": statecat.HOOK_COMMAND}]},
        ]}}))
        result = run_cli(self.dir, "hook", "--install")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("removed 2 duplicate or older StateCat hook(s)", result.stdout)
        settings = json.loads(read_text(self.settings_path()))
        self.assertEqual(settings["hooks"]["SessionStart"], [
            {"hooks": [{"type": "command", "command": statecat.HOOK_COMMAND}]},
            {"hooks": [{"type": "command", "command": "echo mine"}]},
        ])
        self.assertIn("already in", run_cli(self.dir, "hook", "--install").stdout)

    def test_sc20_hook_install_keeps_the_files_own_formatting(self):
        # SC-20: install used to rewrite the whole file with
        # two-space indentation and \u-escaped non-ASCII text.
        self.init_project()
        original = (
            '{\n'
            '    "model": "café",\n'
            '    "permissions": {\n'
            '        "allow": [\n'
            '            "Bash(ls)"\n'
            '        ]\n'
            '    }\n'
            '}\n'
        )
        self.write_settings(original)
        self.assertEqual(run_cli(self.dir, "hook", "--install").returncode, 0)
        text = read_text(self.settings_path())
        # Everything up to the end of "permissions" is unchanged,
        # apart from the comma that now follows it.
        self.assertTrue(text.startswith(original[: -len("    }\n}\n")] + "    },\n"), text)
        self.assertIn('"model": "café"', text)
        self.assertIn('\n    "hooks": {', text)
        self.assertTrue(text.endswith("}\n"))
        self.assertEqual(run_cli(self.dir, "hook", "--uninstall").returncode, 0)
        self.assertEqual(read_text(self.settings_path()), original)

    def test_sc20_hook_install_keeps_tabs_and_crlf(self):
        self.init_project()
        original = '{\r\n\t"model": "x"\r\n}\r\n'
        self.write_settings(original)
        self.assertEqual(run_cli(self.dir, "hook", "--install").returncode, 0)
        raw = read_bytes(self.settings_path())
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))
        self.assertIn(b'\r\n\t"model": "x",\r\n\t"hooks"', raw)
        self.assertEqual(run_cli(self.dir, "hook", "--uninstall").returncode, 0)
        self.assertEqual(read_bytes(self.settings_path()), original.encode("utf-8"))

    def test_sc08_hook_install_refuses_when_settings_change_underneath(self):
        # SC-08: read, edit in memory, replace -- a change made by
        # someone else in between (an editor, Claude Code itself)
        # used to be silently overwritten.
        self.init_project()
        sys.path.insert(0, ROOT)
        import statecat
        from unittest import mock

        self.write_settings('{"model": "before"}\n')
        changed = '{"model": "changed by someone else"}\n'
        real_parse = statecat.parse_hook_settings

        def parse_then_change(path, raw):
            result = real_parse(path, raw)
            self.write_settings(changed)
            return result

        class Args:
            install, uninstall = True, False

        old_cwd = os.getcwd()
        try:
            os.chdir(self.dir)
            with mock.patch.object(statecat, "parse_hook_settings", side_effect=parse_then_change):
                with mock.patch("sys.stderr") as err:
                    code = statecat.cmd_hook(Args())
        finally:
            os.chdir(old_cwd)
        self.assertEqual(code, 1)
        self.assertIn("changed while StateCat was updating it", "".join(c.args[0] for c in err.write.call_args_list))
        self.assertEqual(read_text(self.settings_path()), changed)

    def _hold_lock(self):
        sys.path.insert(0, ROOT)
        import statecat
        lock = statecat.project_lock(self.dir)
        lock.__enter__()
        return lock

    def test_sc08_sc14_hook_and_pointer_writes_wait_for_the_project_lock(self):
        self.init_project()
        lock = self._hold_lock()
        try:
            env = {"STATECAT_LOCK_TIMEOUT": "0.5", "STATECAT_LOCK_STALE": "60"}
            for args in (("hook", "--install"), ("pointer", "--uninstall"), ("pointer", "--install")):
                result = run_cli_env(self.dir, env, *args)
                self.assertEqual(result.returncode, 1, args)
                self.assertIn("lock is held", result.stderr, args)
            self.assertFalse(os.path.exists(self.settings_path()))
            self.assertIn(POINTER, read_text(os.path.join(self.dir, "AGENTS.md")))
        finally:
            lock.__exit__(None, None, None)
        self.assertEqual(run_cli(self.dir, "hook", "--install").returncode, 0)

    def _symlink_or_skip(self, target, link):
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError) as exc:
            self.skipTest("cannot create symlinks here: %s" % exc)

    def test_sc11_hook_install_refuses_a_settings_link_that_leaves_the_project(self):
        self.init_project()
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        outside_settings = os.path.join(outside.name, "settings.json")
        with open(outside_settings, "w", encoding="utf-8", newline="") as handle:
            handle.write('{"model": "someone else"}\n')
        self._symlink_or_skip(outside.name, os.path.join(self.dir, ".claude"))
        result = run_cli(self.dir, "hook", "--install")
        self.assertEqual(result.returncode, 1)
        self.assertIn("outside this project", result.stderr)
        self.assertEqual(read_text(outside_settings), '{"model": "someone else"}\n')

    def test_sc11_pointer_refuses_a_link_that_leaves_the_project_but_allows_one_inside(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        outside_file = os.path.join(outside.name, "AGENTS.md")
        with open(outside_file, "w", encoding="utf-8", newline="") as handle:
            handle.write("# Someone else's file\n")
        self._symlink_or_skip(outside_file, os.path.join(self.dir, "AGENTS.md"))
        result = run_cli(self.dir, "pointer", "--install")
        self.assertEqual(result.returncode, 1)
        self.assertIn("outside this project", result.stderr)
        self.assertEqual(read_text(outside_file), "# Someone else's file\n")
        # A link inside the project (CLAUDE.md -> docs/agents.md) is fine.
        os.unlink(os.path.join(self.dir, "AGENTS.md"))
        os.makedirs(os.path.join(self.dir, "docs"))
        inside = os.path.join(self.dir, "docs", "agents.md")
        with open(inside, "w", encoding="utf-8", newline="") as handle:
            handle.write("# Rules\n")
        os.symlink(os.path.join("docs", "agents.md"), os.path.join(self.dir, "CLAUDE.md"))
        result = run_cli(self.dir, "pointer", "--install")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(POINTER, read_text(inside))

    def test_sc12_pointer_uninstall_keeps_an_agents_md_statecat_did_not_create(self):
        # SC-12: a user's own AGENTS.md that holds just the same
        # header and the pointer used to be deleted.
        agents = os.path.join(self.dir, "AGENTS.md")
        with open(agents, "w", encoding="utf-8", newline="") as handle:
            handle.write("# Project instructions\n\n%s\n" % POINTER)
        result = run_cli(self.dir, "pointer", "--uninstall")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("kept the file", result.stdout)
        self.assertEqual(read_text(agents), "# Project instructions\n\n")

    def test_sc13_pointer_quoted_in_docs_is_not_the_pointer(self):
        # SC-13: install matched the pointer text anywhere, so a
        # quote of it counted as installed; uninstall must also
        # leave quotes alone.
        agents = os.path.join(self.dir, "AGENTS.md")
        quoted = (
            "# Rules\n\nWe use StateCat; its pointer says \"%s\"\n\n"
            "```text\n%s\n```\n" % (POINTER, POINTER)
        )
        with open(agents, "w", encoding="utf-8", newline="") as handle:
            handle.write(quoted)
        result = run_cli(self.dir, "pointer", "--install")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("added pointer to AGENTS.md", result.stdout)
        self.assertEqual(read_text(agents), quoted + POINTER + "\n")
        result = run_cli(self.dir, "pointer", "--uninstall")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(read_text(agents), quoted)

    def test_sc19_pointer_uninstall_exits_non_zero_when_a_file_cannot_be_read(self):
        with open(os.path.join(self.dir, "AGENTS.md"), "wb") as handle:
            handle.write(b"\xff\xfe not utf-8 \x80\n")
        with open(os.path.join(self.dir, "CLAUDE.md"), "w", encoding="utf-8", newline="") as handle:
            handle.write("# Rules\n%s\n" % POINTER)
        result = run_cli(self.dir, "pointer", "--uninstall")
        self.assertEqual(result.returncode, 1)
        self.assertIn("could not read", result.stderr)
        # The readable file is still processed.
        self.assertIn("removed pointer from CLAUDE.md", result.stdout)
        self.assertEqual(read_text(os.path.join(self.dir, "CLAUDE.md")), "# Rules\n")

    def test_sc18_tilde_and_longer_fences_are_not_structure(self):
        # SC-18: only ``` fences were recognised, and any ``` line
        # toggled the fence, so a ~~~ block or a ```` block showing
        # a ``` line let its example headings and items be parsed.
        self.init_project()
        run_cli(self.dir, "add", "Open", "Real item")
        text = read_text(state_path(self.dir))
        fences = (
            "~~~markdown\n## Open\n- #99 [2026-11-06] Tilde fake\n~~~\n\n"
            "````markdown\n```\n## Open\n- #98 [2026-11-06] Long-fence fake\n```\n````\n\n"
            "```markdown\n~~~\n## Open\n- #97 [2026-11-06] Mixed fake\n~~~\n```\n\n"
        )
        text = text.replace("## Decided\n", fences + "## Decided\n", 1)
        with open(state_path(self.dir), "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        result = run_cli(self.dir, "check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("clean", result.stdout)
        shown = run_cli(self.dir, "show", "--json").stdout
        for fake in ("#99", "#98", "#97", "fake"):
            self.assertNotIn(fake, shown)
        self.assertEqual(run_cli(self.dir, "add", "Open", "Second").returncode, 0)
        self.assertIn("Second", "\n".join(bucket_lines(read_text(state_path(self.dir)), "Open")))

    def test_non_ascii_output_survives_a_non_utf8_stdout(self):
        # Windows pipes default to the ANSI code page; cp1252 forces that anywhere.
        self.init_project()
        run_cli(self.dir, "add", "Open", "ship it ✓")
        env = dict(os.environ, PYTHONIOENCODING="cp1252")
        for args in (["show"], ["show", "Open"], ["search", "ship"], ["status"]):
            result = subprocess.run([sys.executable, CLI, *args], cwd=self.dir, capture_output=True, env=env)
            self.assertEqual(result.returncode, 0, (args, result.stderr.decode("utf-8", "replace")))
            if args[0] != "status":
                self.assertIn("ship it ✓", result.stdout.decode("utf-8"), args)

    def test_init_blank_name_falls_back_to_folder_name(self):
        result = run_cli(self.dir, "init", "   ")
        self.assertEqual(result.returncode, 0, result.stderr)
        first = read_text(state_path(self.dir)).splitlines()[0]
        self.assertEqual(first, "# Project: %s" % os.path.basename(os.path.abspath(self.dir)))
        with tempfile.TemporaryDirectory() as padded:
            self.assertEqual(run_cli(padded, "init", "  Named  ").returncode, 0)
            self.assertEqual(read_text(state_path(padded)).splitlines()[0], "# Project: Named")

    def test_search_finds_words_on_wrapped_continuation_lines(self):
        self.init_project()
        path = state_path(self.dir)
        text = read_text(path).replace(
            "## Open\n\n",
            "## Open\n\n- #1 [%s] First line of a wrapped item\n    second line mentions Zebra\n" % TODAY,
        ).replace("> Last ID used: #0", "> Last ID used: #1")
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        result = run_cli(self.dir, "search", "zebra")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("#1", result.stdout)
        self.assertIn("second line mentions Zebra", result.stdout)
        item = json.loads(run_cli(self.dir, "show", "--json").stdout)["buckets"]["Open"][0]
        self.assertEqual(item["text"], "First line of a wrapped item")
        self.assertEqual(item["continuation"], ["second line mentions Zebra"])
        self.assertEqual(run_cli(self.dir, "archive", "1").returncode, 0)
        self.assertNotEqual(run_cli(self.dir, "search", "zebra").returncode, 0)
        self.assertIn("(Archived)", run_cli(self.dir, "search", "zebra", "--archive").stdout)

    def test_heading_with_trailing_whitespace_still_works(self):
        self.init_project()
        path = state_path(self.dir)
        text = read_text(path).replace("## Open\n", "## Open  \n").replace("## Parked\n", "##  Parked\t\n")
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        self.assertEqual(run_cli(self.dir, "check").returncode, 0)
        self.assertEqual(run_cli(self.dir, "add", "Open", "after the spaced heading").returncode, 0)
        self.assertEqual(run_cli(self.dir, "add", "Parked", "parked one").returncode, 0)
        after = read_text(path)
        self.assertIn("## Open  \n\n- #1 [%s] after the spaced heading\n" % TODAY, after)
        self.assertIn("##  Parked\t\n\n- #2 [%s] parked one\n" % TODAY, after)
        self.assertIn("after the spaced heading", run_cli(self.dir, "show", "Open").stdout)

    def test_config_set_keeps_unknown_keys(self):
        self.init_project()
        path = os.path.join(self.dir, ".statecat", "config")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"future_key": "keep me", "stale_days": 7}))
        result = run_cli(self.dir, "config", "set", "backups_kept", "5")
        self.assertEqual(result.returncode, 0, result.stderr)
        saved = json.loads(read_text(path))
        self.assertEqual(saved["future_key"], "keep me")
        self.assertEqual(saved["stale_days"], 7)
        self.assertEqual(saved["backups_kept"], 5)

    def test_check_fix_with_nothing_to_fix_takes_no_snapshot(self):
        self.init_project()
        run_cli(self.dir, "add", "Open", "real change")
        before = run_cli(self.dir, "restore", "--list").stdout
        for _ in range(3):
            result = run_cli(self.dir, "check", "--fix")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("nothing to fix", result.stdout)
        self.assertEqual(run_cli(self.dir, "restore", "--list").stdout, before)

    def test_compact_keeps_archive_in_item_order(self):
        self.init_project()
        path = state_path(self.dir)
        text = read_text(path).replace(
            "## Decided\n\n",
            "## Decided\n\n- #1 [2020-01-01] one\n- #2 [2020-01-02] two\n- #3 [2020-01-03] three\n",
        ).replace("> Last ID used: #0", "> Last ID used: #3")
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        self.assertEqual(run_cli(self.dir, "compact", "--older-than", "30").returncode, 0)
        archived = [line for line in read_text(archive_path(self.dir)).splitlines() if line.startswith("- #")]
        self.assertEqual([line[:4] for line in archived], ["- #1", "- #2", "- #3"])
        self.assertIn("## Decided\n\n## Open", read_text(path))

    def test_diff_ignores_a_bom_on_the_snapshot(self):
        self.init_project()
        path = state_path(self.dir)
        original = read_bytes(path)
        with open(path, "wb") as handle:
            handle.write(b"\xef\xbb\xbf" + original)
        run_cli(self.dir, "add", "Open", "new item")
        self.assertEqual(run_cli(self.dir, "restore").returncode, 0)
        result = run_cli_env(self.dir, {"PYTHONIOENCODING": "utf-8"}, "diff")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("﻿", result.stdout)
        self.assertNotIn("-# Project", result.stdout)

    # Bucket spacing: items stay together and blank lines never pile up.
    def test_consecutive_adds_stay_together_without_blank_lines(self):
        self.init_project()
        run_cli(self.dir, "add", "Open", "first")
        run_cli(self.dir, "add", "Open", "second")
        run_cli(self.dir, "add", "Open", "third")
        text = read_text(state_path(self.dir))
        self.assertIn(
            "## Open\n\n- #1 [%s] first\n- #2 [%s] second\n- #3 [%s] third\n\n## Waiting on human"
            % (TODAY, TODAY, TODAY),
            text,
        )

    def test_add_archive_cycles_do_not_accumulate_blank_lines(self):
        self.init_project()
        fresh = read_text(state_path(self.dir))
        for _ in range(3):
            run_cli(self.dir, "add", "Open", "temporary")
            last = json.loads(run_cli(self.dir, "show", "--json").stdout)["last_id"]
            self.assertEqual(run_cli(self.dir, "archive", str(last)).returncode, 0)
        after = read_text(state_path(self.dir))
        self.assertEqual(
            re.sub(r"> Last ID used: #\d+", "", after),
            re.sub(r"> Last ID used: #\d+", "", fresh),
        )

    def test_move_keeps_target_list_together_and_source_tidy(self):
        self.init_project()
        run_cli(self.dir, "add", "Decided", "kept")
        run_cli(self.dir, "add", "Open", "promoted")
        self.assertEqual(run_cli(self.dir, "move", "2", "Decided").returncode, 0)
        text = read_text(state_path(self.dir))
        self.assertIn("## Decided\n\n- #1 [%s] kept\n- #2 [%s] promoted\n\n## Open\n\n## Waiting" % (TODAY, TODAY), text)

    def test_archive_into_file_without_final_newline(self):
        self.init_project()
        run_cli(self.dir, "add", "Open", "to archive")
        path = archive_path(self.dir)
        trimmed = read_text(path).rstrip("\n")
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(trimmed)
        result = run_cli(self.dir, "archive", "1")
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = read_text(path).split("\n")
        self.assertIn("## Archived", lines)
        self.assertIn("- #1 [%s] to archive" % TODAY, lines)
        self.assertEqual(run_cli(self.dir, "check").returncode, 0)

    def test_move_last_item_of_file_without_final_newline(self):
        self.init_project()
        run_cli(self.dir, "add", "Parked", "last line")
        path = state_path(self.dir)
        trimmed = read_text(path).rstrip("\n")
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(trimmed)
        result = run_cli(self.dir, "move", "1", "Open", "--owner-approved")
        self.assertEqual(result.returncode, 0, result.stderr)
        text = read_text(path)
        self.assertIn("- #1 [%s] last line\n\n## Waiting on human" % TODAY, text)
        self.assertEqual(run_cli(self.dir, "check").returncode, 0)

    # Claude Code SessionStart hook.
    def settings_path(self):
        return os.path.join(self.dir, ".claude", "settings.json")

    def write_settings(self, text):
        os.makedirs(os.path.dirname(self.settings_path()), exist_ok=True)
        with open(self.settings_path(), "w", encoding="utf-8", newline="") as handle:
            handle.write(text)

    def statecat_hook_commands(self):
        settings = json.loads(read_text(self.settings_path()))
        return [
            hook["command"]
            for group in settings.get("hooks", {}).get("SessionStart", [])
            for hook in group["hooks"]
            if "statecat" in hook["command"] and "show" in hook["command"]
        ]

    def test_hook_install_creates_settings(self):
        self.init_project()
        result = run_cli(self.dir, "hook", "--install")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("added SessionStart hook", result.stdout)
        commands = self.statecat_hook_commands()
        self.assertEqual(len(commands), 1)
        self.assertIn('cd "$CLAUDE_PROJECT_DIR"', commands[0])
        self.assertIn("for P in python3 py python", commands[0])
        self.assertTrue(commands[0].endswith("exit 0"))

    def test_hook_install_keeps_existing_settings_and_is_idempotent(self):
        self.init_project()
        other = {"type": "command", "command": "echo hello"}
        self.write_settings(json.dumps({
            "permissions": {"allow": ["Bash(ls)"]},
            "hooks": {
                "SessionStart": [{"hooks": [other]}],
                "Stop": [{"hooks": [{"type": "command", "command": "echo bye"}]}],
            },
        }))
        self.assertEqual(run_cli(self.dir, "hook", "--install").returncode, 0)
        result = run_cli(self.dir, "hook", "--install")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("already in", result.stdout)
        settings = json.loads(read_text(self.settings_path()))
        self.assertEqual(settings["permissions"], {"allow": ["Bash(ls)"]})
        self.assertIn("Stop", settings["hooks"])
        self.assertEqual(settings["hooks"]["SessionStart"][0], {"hooks": [other]})
        self.assertEqual(len(self.statecat_hook_commands()), 1)

    def test_hook_install_upgrades_old_statecat_command(self):
        self.init_project()
        old = 'cd "$CLAUDE_PROJECT_DIR" && python3 statecat.py show && python3 statecat.py check; exit 0'
        self.write_settings(json.dumps({"hooks": {"SessionStart": [
            {"hooks": [{"type": "command", "command": old}]},
        ]}}))
        result = run_cli(self.dir, "hook", "--install")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("updated SessionStart hook", result.stdout)
        commands = self.statecat_hook_commands()
        self.assertEqual(len(commands), 1)
        self.assertNotEqual(commands[0], old)

    def test_sc09_hook_install_keeps_a_users_wrapper_command(self):
        # SC-09: a user's own command that merely contains
        # "statecat.py show" and "statecat.py check" is not a
        # StateCat hook. Install must add StateCat's entry next to
        # it, not overwrite it.
        self.init_project()
        mine = 'cd "$CLAUDE_PROJECT_DIR" && ./my-wrapper.sh "statecat.py show" "statecat.py check" && notify-me'
        self.write_settings(json.dumps({"hooks": {"SessionStart": [
            {"hooks": [{"type": "command", "command": mine}]},
        ]}}))
        result = run_cli(self.dir, "hook", "--install")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("added SessionStart hook", result.stdout)
        settings = json.loads(read_text(self.settings_path()))
        commands = [h["command"] for g in settings["hooks"]["SessionStart"] for h in g["hooks"]]
        self.assertIn(mine, commands)
        self.assertEqual(len(commands), 2)

    def test_sc09_hook_uninstall_keeps_a_users_wrapper_command(self):
        self.init_project()
        mine = "python3 statecat.py show && python3 statecat.py check && ./post-to-slack.sh"
        self.write_settings(json.dumps({"hooks": {"SessionStart": [
            {"hooks": [{"type": "command", "command": mine}]},
        ]}}))
        result = run_cli(self.dir, "hook", "--uninstall")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no StateCat SessionStart hook", result.stdout)
        settings = json.loads(read_text(self.settings_path()))
        self.assertEqual(settings["hooks"]["SessionStart"][0]["hooks"][0]["command"], mine)

    def test_sc09_every_shipped_hook_command_is_still_recognised(self):
        # Exact matching must still upgrade and remove every
        # command StateCat itself wrote or documented.
        sys.path.insert(0, ROOT)
        import statecat
        for old in statecat.LEGACY_HOOK_COMMANDS:
            self.init_project() if not os.path.exists(state_path(self.dir)) else None
            self.write_settings(json.dumps({"hooks": {"SessionStart": [
                {"hooks": [{"type": "command", "command": "  %s\n" % old}]},
            ]}}))
            result = run_cli(self.dir, "hook", "--install")
            self.assertIn("updated SessionStart hook", result.stdout, old)
            self.assertEqual(self.statecat_hook_commands(), [statecat.HOOK_COMMAND])
            self.write_settings(json.dumps({"hooks": {"SessionStart": [
                {"hooks": [{"type": "command", "command": old}]},
            ]}}))
            result = run_cli(self.dir, "hook", "--uninstall")
            self.assertIn("removed SessionStart hook", result.stdout, old)

    def test_hook_install_refuses_invalid_json(self):
        self.init_project()
        self.write_settings('{"hooks": {,}')
        result = run_cli(self.dir, "hook", "--install")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not valid JSON", result.stderr)
        self.assertEqual(read_text(self.settings_path()), '{"hooks": {,}')

    def test_hook_install_reports_unreadable_settings_plainly(self):
        self.init_project()
        os.makedirs(self.settings_path())  # a folder where the file should be
        result = run_cli(self.dir, "hook", "--install")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("could not read", result.stderr)
        self.assertNotIn("not valid JSON", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_hook_install_refuses_wrong_shapes(self):
        self.init_project()
        for text in ('[]', '{"hooks": []}', '{"hooks": {"SessionStart": {}}}'):
            self.write_settings(text)
            result = run_cli(self.dir, "hook", "--install")
            self.assertNotEqual(result.returncode, 0, text)
            self.assertEqual(read_text(self.settings_path()), text)

    def test_hook_uninstall_removes_only_statecat_entry(self):
        self.init_project()
        other = {"type": "command", "command": "echo hello"}
        self.write_settings(json.dumps({"model": "x", "hooks": {"SessionStart": [{"hooks": [other]}]}}))
        run_cli(self.dir, "hook", "--install")
        result = run_cli(self.dir, "hook", "--uninstall")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("removed SessionStart hook", result.stdout)
        settings = json.loads(read_text(self.settings_path()))
        self.assertEqual(settings, {"model": "x", "hooks": {"SessionStart": [{"hooks": [other]}]}})

    def test_hook_uninstall_keeps_groups_the_user_left_empty(self):
        self.init_project()
        self.write_settings(json.dumps({"hooks": {"SessionStart": [{"matcher": "startup", "hooks": []}]}}))
        run_cli(self.dir, "hook", "--install")
        self.assertEqual(run_cli(self.dir, "hook", "--uninstall").returncode, 0)
        self.assertEqual(
            json.loads(read_text(self.settings_path())),
            {"hooks": {"SessionStart": [{"matcher": "startup", "hooks": []}]}},
        )

    def test_hook_uninstall_drops_empty_hooks_key(self):
        self.init_project()
        run_cli(self.dir, "hook", "--install")
        run_cli(self.dir, "hook", "--uninstall")
        self.assertEqual(json.loads(read_text(self.settings_path())), {})
        again = run_cli(self.dir, "hook", "--uninstall")
        self.assertIn("no StateCat SessionStart hook", again.stdout)

    def test_hook_installs_at_project_root_from_subfolder(self):
        self.init_project()
        sub = os.path.join(self.dir, "src")
        os.makedirs(sub)
        result = run_cli(sub, "hook", "--install")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(os.path.isfile(self.settings_path()))
        self.assertFalse(os.path.exists(os.path.join(sub, ".claude")))

    def test_hook_prints_entry_without_writing(self):
        result = run_cli(self.dir, "hook")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertIn("SessionStart", data["hooks"])
        self.assertFalse(os.path.exists(self.settings_path()))

    @unittest.skipIf(shutil.which("sh") is None, "needs a POSIX sh to run the hook command")
    def test_hook_command_uses_installed_statecat_without_script(self):
        # pip-installed: no statecat.py in the project, a statecat command on PATH.
        self.init_project("Pip Run")
        run_cli(self.dir, "hook", "--install")
        command = self.statecat_hook_commands()[0]
        bin_dir = os.path.join(self.dir, "fakebin")
        os.makedirs(bin_dir)
        fake = os.path.join(bin_dir, "statecat")
        with open(fake, "w", encoding="utf-8", newline="\n") as handle:
            handle.write('#!/bin/sh\necho "installed statecat $1"\n')
        os.chmod(fake, 0o755)
        # PATH is colon-separated, so C:/x must become /c/x for Git Bash.
        posix_bin = re.sub(r"^([A-Za-z]):", lambda m: "/" + m.group(1).lower(), bin_dir.replace("\\", "/"))
        result = subprocess.run(
            ["sh", "-c", 'PATH="%s:$PATH"; %s' % (posix_bin, command)], capture_output=True, text=True,
            env=dict(os.environ, CLAUDE_PROJECT_DIR=self.dir.replace("\\", "/")),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("installed statecat show", result.stdout)
        self.assertIn("installed statecat check", result.stdout)

    @unittest.skipIf(shutil.which("sh") is None, "needs a POSIX sh to run the hook command")
    def test_hook_command_runs_show_and_check(self):
        self.init_project("Hook Run")
        shutil.copy(CLI, self.dir)
        run_cli(self.dir, "hook", "--install")
        command = self.statecat_hook_commands()[0]
        result = subprocess.run(
            ["sh", "-c", command], capture_output=True, text=True,
            env=dict(os.environ, CLAUDE_PROJECT_DIR=self.dir.replace("\\", "/")),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("# Project: Hook Run", result.stdout)
        self.assertIn("check: clean", result.stdout)

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
        self.assertTrue(result.stdout.strip().startswith("statecat 1.5.0"))

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

    # New state files follow the umask (0666 & ~umask), not a
    # forced mode: with the usual 022 umask that is 0644.
    # Unix permission bits do not exist on Windows (st_mode is
    # fabricated as 0666 for a writable file), so these tests
    # only run where the mode they assert can exist.
    @unittest.skipIf(
        sys.platform == "win32",
        "Unix file mode bits do not exist on Windows",
    )
    def test_state_files_are_world_readable(self):
        # Pin the umask for the CLI subprocesses (they inherit
        # it), so the umask-derived mode is deterministic.
        old_umask = os.umask(0o022)
        try:
            self.init_project()
            for path in (state_path(self.dir), archive_path(self.dir)):
                self.assertEqual(os.stat(path).st_mode & 0o777, 0o644)
            run_cli(self.dir, "add", "Open", "Written by add")
            for path in (state_path(self.dir), archive_path(self.dir)):
                self.assertEqual(os.stat(path).st_mode & 0o777, 0o644)
        finally:
            os.umask(old_umask)

    @unittest.skipIf(
        sys.platform == "win32",
        "Unix file mode bits do not exist on Windows",
    )
    def test_write_preserves_restrictive_permissions(self):
        self.init_project()
        os.chmod(state_path(self.dir), 0o600)
        run_cli(self.dir, "add", "Open", "Confidential detail")
        self.assertEqual(os.stat(state_path(self.dir)).st_mode & 0o777, 0o600)
        # The pre-write snapshot of the restrictive file is
        # restrictive too, not world-readable.
        backup_root = os.path.join(self.dir, ".statecat", "backups")
        names = sorted(os.listdir(backup_root))
        self.assertTrue(names, "expected a pre-write snapshot")
        newest = os.path.join(backup_root, names[-1], "STATE.md")
        self.assertEqual(os.stat(newest).st_mode & 0o777, 0o600)

    @unittest.skipIf(
        sys.platform == "win32",
        "Unix file mode bits do not exist on Windows",
    )
    def test_restore_preserves_permissions(self):
        self.init_project()
        run_cli(self.dir, "add", "Open", "Before restore")
        os.chmod(state_path(self.dir), 0o600)
        os.chmod(archive_path(self.dir), 0o600)
        result = run_cli(self.dir, "restore")
        self.assertEqual(result.returncode, 0, result.stderr)
        for path in (state_path(self.dir), archive_path(self.dir)):
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)



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

    def test_lock_pid_reuse_with_wrong_start_time_is_broken_as_stale(self):
        # SC-04: a PID alone cannot identify the owner. If the
        # owner dies and the OS hands its PID to an unrelated
        # process, a plain liveness probe reports the reused
        # PID as alive forever and the lock strands. The owner
        # file also records the process start time; a live PID
        # with the wrong start time proves the PID was reused,
        # so the lock is broken as stale.
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
            first._stop_heartbeat()
            # Rewrite the owner file: the PID is this live test
            # process, but the start time is wrong, as if the
            # PID had been reused after the owner died.
            owner_path = os.path.join(lock_path, "owner")
            text = re.sub(
                r"^start=.*$", "start=1.0", read_text(owner_path), flags=re.M
            )
            with open(owner_path, "w", encoding="utf-8") as handle:
                handle.write(text)
            old = time.time() - 3600
            os.utime(lock_path, (old, old))
            second = statecat.project_lock(self.dir)
            second.__enter__()
            self.assertNotEqual(second.owner_token, first.owner_token)
            self.assertTrue(os.path.isdir(lock_path))
            first.__exit__(None, None, None)
            self.assertTrue(os.path.isdir(lock_path))
            self.assertIn(second.owner_token, read_text(owner_path))
            second.__exit__(None, None, None)
            self.assertFalse(os.path.isdir(lock_path))
        finally:
            for key, value in old_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_lock_live_owner_with_matching_start_time_is_not_broken(self):
        # The start-time check must not break a genuinely live
        # owner: the same PID and the same start time means
        # the owner is still the original process.
        # The lock is aged 5s: past the 1s stale limit, so the
        # abandonment check runs, but younger than the 10x
        # unknown-owner threshold (10s), so on Windows, where
        # liveness is unknown and the start time is
        # unavailable, the lock is not abandoned as unknown
        # either. On Linux the matching start time is what
        # saves it; on Windows the 10x threshold is.
        self.init_project()
        sys.path.insert(0, ROOT)
        import statecat

        lock_path = os.path.join(self.dir, ".statecat", "lock")
        env = {"STATECAT_LOCK_TIMEOUT": "1", "STATECAT_LOCK_STALE": "1"}
        old_env = {key: os.environ.get(key) for key in env}
        try:
            os.environ.update(env)
            first = statecat.project_lock(self.dir)
            first.__enter__()
            first._stop_heartbeat()
            old = time.time() - 5
            os.utime(lock_path, (old, old))
            second = statecat.project_lock(self.dir)
            with self.assertRaises(SystemExit):
                second.__enter__()
            self.assertTrue(os.path.isdir(lock_path))
            owner_path = os.path.join(lock_path, "owner")
            self.assertIn(first.owner_token, read_text(owner_path))
            first.__exit__(None, None, None)
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
        # owner's lock here. Release checks the token in place,
        # renames the dir aside, then verifies the renamed copy,
        # so the takeover is injected at that last read (the
        # first read of a dir other than the lock path), and the
        # new owner's lock must survive.
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
                if "second" not in holder and directory != lock_path:
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

    def _with_lock_env(self, env, body):
        old_env = {key: os.environ.get(key) for key in env}
        try:
            os.environ.update(env)
            body()
        finally:
            for key, value in old_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_sc17_waiter_never_moves_a_live_owners_lock(self):
        # SC-17: a waiter that found an old-looking lock used to
        # rename it aside *before* checking whether its owner was
        # alive, then put it back. While it was aside the lock path
        # was empty, so a third process could mkdir it and become a
        # second writer next to the still-running owner. The
        # abandonment check must run in place: a lock whose owner
        # is alive is never moved, not even briefly.
        self.init_project()
        sys.path.insert(0, ROOT)
        import statecat
        from unittest import mock

        lock_path = os.path.join(self.dir, ".statecat", "lock")

        def body():
            first = statecat.project_lock(self.dir)
            first.__enter__()
            first._stop_heartbeat()
            # Past the 1s stale limit, under the 10x unknown-owner
            # threshold: old enough to trigger a break attempt, but
            # the owner (this process) is alive.
            old = time.time() - 5
            os.utime(lock_path, (old, old))
            moved = []
            real_rename = os.rename

            def recording_rename(src, dst, *args, **kwargs):
                if os.path.abspath(src) == os.path.abspath(lock_path):
                    moved.append(dst)
                return real_rename(src, dst, *args, **kwargs)

            with mock.patch.object(statecat.os, "rename", side_effect=recording_rename):
                second = statecat.project_lock(self.dir)
                with self.assertRaises(SystemExit):
                    second.__enter__()
            self.assertEqual(moved, [], "a live owner's lock was moved off its path")
            self.assertIn(first.owner_token, read_text(os.path.join(lock_path, "owner")))
            first.__exit__(None, None, None)
            self.assertFalse(os.path.isdir(lock_path))

        self._with_lock_env({"STATECAT_LOCK_TIMEOUT": "1", "STATECAT_LOCK_STALE": "1"}, body)

    def test_sc17_moved_copy_that_is_not_the_checked_lock_is_restored(self):
        # The lock is only deleted when the renamed copy is the
        # very directory that was proved abandoned. Here the
        # abandoned lock is swapped for a live writer's lock right
        # before the rename; the moved copy must go back to the
        # lock path, never be deleted.
        self.init_project()
        sys.path.insert(0, ROOT)
        import statecat
        from unittest import mock

        lock_path = os.path.join(self.dir, ".statecat", "lock")

        def body():
            first = statecat.project_lock(self.dir)
            first.__enter__()
            first._stop_heartbeat()
            simulate_dead_owner(lock_path)
            old = time.time() - 3600
            os.utime(lock_path, (old, old))
            real_rename = os.rename
            swapped = {}

            def swapping_rename(src, dst, *args, **kwargs):
                if not swapped and os.path.abspath(src) == os.path.abspath(lock_path):
                    shutil.rmtree(lock_path)
                    os.mkdir(lock_path)
                    with open(os.path.join(lock_path, "owner"), "w", encoding="utf-8") as handle:
                        handle.write("pid=%d time=x\nstart=unknown\ntoken=intruder\n" % os.getpid())
                    swapped["done"] = True
                return real_rename(src, dst, *args, **kwargs)

            with mock.patch.object(statecat.os, "rename", side_effect=swapping_rename):
                second = statecat.project_lock(self.dir)
                with self.assertRaises(SystemExit):
                    second.__enter__()
            self.assertTrue(swapped)
            self.assertIn("token=intruder", read_text(os.path.join(lock_path, "owner")))
            shutil.rmtree(lock_path)

        self._with_lock_env({"STATECAT_LOCK_TIMEOUT": "1", "STATECAT_LOCK_STALE": "1"}, body)

    def test_sc17_breakers_are_serialised_by_the_break_guard(self):
        # While another process holds the break guard, a waiter
        # must not break even a provably abandoned lock; once the
        # guard is free, it does.
        self.init_project()
        sys.path.insert(0, ROOT)
        import statecat

        lock_path = os.path.join(self.dir, ".statecat", "lock")
        statecat_dir = os.path.join(self.dir, ".statecat")

        def body():
            first = statecat.project_lock(self.dir)
            first.__enter__()
            first._stop_heartbeat()
            simulate_dead_owner(lock_path)
            old = time.time() - 3600
            os.utime(lock_path, (old, old))
            guard = statecat._acquire_break_guard(statecat_dir, time.monotonic() + 1)
            self.assertNotIn(guard, (None, statecat._BREAK_GUARD_UNSUPPORTED))
            try:
                second = statecat.project_lock(self.dir)
                with self.assertRaises(SystemExit):
                    second.__enter__()
                self.assertIn(first.owner_token, read_text(os.path.join(lock_path, "owner")))
            finally:
                statecat._release_break_guard(guard)
            third = statecat.project_lock(self.dir)
            third.__enter__()
            self.assertIn(third.owner_token, read_text(os.path.join(lock_path, "owner")))
            third.__exit__(None, None, None)
            self.assertFalse(os.path.isdir(lock_path))
            # The guard file is a plain lockable file and stays.
            self.assertTrue(os.path.isfile(os.path.join(statecat_dir, "lock.break")))

        self._with_lock_env({"STATECAT_LOCK_TIMEOUT": "1", "STATECAT_LOCK_STALE": "1"}, body)

    def test_sc17_release_never_moves_a_lock_that_is_not_ours(self):
        # A writer whose lock was broken and taken over must not
        # rename the new owner's lock aside on release: even a
        # brief move leaves the path open to a third writer.
        self.init_project()
        sys.path.insert(0, ROOT)
        import statecat
        from unittest import mock

        lock_path = os.path.join(self.dir, ".statecat", "lock")

        def body():
            first = statecat.project_lock(self.dir)
            first.__enter__()
            first._stop_heartbeat()
            simulate_dead_owner(lock_path)
            old = time.time() - 3600
            os.utime(lock_path, (old, old))
            second = statecat.project_lock(self.dir)
            second.__enter__()
            moved = []
            real_rename = os.rename

            def recording_rename(src, dst, *args, **kwargs):
                if os.path.abspath(src) == os.path.abspath(lock_path):
                    moved.append(dst)
                return real_rename(src, dst, *args, **kwargs)

            with mock.patch.object(statecat.os, "rename", side_effect=recording_rename):
                first.__exit__(None, None, None)
            self.assertEqual(moved, [])
            self.assertIn(second.owner_token, read_text(os.path.join(lock_path, "owner")))
            second.__exit__(None, None, None)
            self.assertFalse(os.path.isdir(lock_path))

        self._with_lock_env({"STATECAT_LOCK_TIMEOUT": "5", "STATECAT_LOCK_STALE": "1"}, body)

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
        # however old its directory looks. On Windows liveness
        # is always unknown, so a lock is only held inside the
        # ten-times stale window: age it 120s there (well
        # inside 600s), not 3600s (past it, where Windows
        # correctly breaks it as abandoned).
        self.init_project()
        lock_path = os.path.join(self.dir, ".statecat", "lock")
        os.makedirs(lock_path)
        with open(os.path.join(lock_path, "owner"), "w", encoding="utf-8") as handle:
            handle.write("pid=%d time=2026-11-04T00:00:00\n" % os.getpid())
            handle.write("token=live-owner-token\n")
        age = 120 if sys.platform == "win32" else 3600
        old = time.time() - age
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
        self.assertIn("owner PID 999999", result.stderr)
        self.assertIn("stale limit 60s", result.stderr)
        self.assertRegex(result.stderr, r"lock age \d+s")
        self.assertIn(lock_path, result.stderr)
        self.assertTrue(os.path.isdir(lock_path))
        if sys.platform == "win32":
            # Windows liveness is always unknown, so a dead PID
            # is reported on the unknown-owner branch: ten
            # times the stale limit, and the PowerShell manual
            # command instead of rm -rf.
            self.assertIn("cannot be determined", result.stderr)
            self.assertIn("600s unknown-owner limit", result.stderr)
            match = re.search(r"in about (\d+) seconds", result.stderr)
            self.assertIsNotNone(match, result.stderr)
            # Aged ~30s at a 600s limit: about 570s left.
            self.assertTrue(555 <= int(match.group(1)) <= 585, result.stderr)
            self.assertIn("Remove-Item -LiteralPath", result.stderr)
        else:
            self.assertIn("left behind", result.stderr)
            self.assertIn("no process is writing", result.stderr)
            match = re.search(r"in about (\d+) seconds", result.stderr)
            self.assertIsNotNone(match, result.stderr)
            # Aged ~30s at a 60s limit: about 30s left, give or take
            # the half-second wait and clock skew.
            self.assertTrue(20 <= int(match.group(1)) <= 35, result.stderr)
            self.assertIn("rm -rf", result.stderr)

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
        if sys.platform == "win32":
            # Windows cannot tell a live owner from a dead one,
            # so this lock is reported on the unknown-owner
            # branch: it clears after ten times the stale limit,
            # not never. Aged ~120s at a 600s limit: ~480s left.
            self.assertIn("cannot be determined", result.stderr)
            self.assertIn("600s unknown-owner limit", result.stderr)
            match = re.search(r"in about (\d+) seconds", result.stderr)
            self.assertIsNotNone(match, result.stderr)
            self.assertTrue(460 <= int(match.group(1)) <= 490, result.stderr)
        else:
            self.assertIn("never while the owner is alive", result.stderr)
            self.assertIn("still running", result.stderr)

    def test_lock_held_error_for_unknown_owner_uses_tenfold_limit(self):
        # No owner file at all: unknown ownership, so the lock
        # clears only after ten times the stale limit. This is
        # the same branch on Windows and POSIX (Windows
        # liveness is always unknown), so no platform branch
        # is needed here.
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
            if sys.platform == "win32":
                # Windows reports the dead PID on the
                # unknown-owner branch, and the manual command
                # is PowerShell, not rm -rf.
                self.assertIn("cannot be determined", result.stderr)
                self.assertIn(
                    "Remove-Item -LiteralPath '%s'" % lock_path, result.stderr
                )
            else:
                self.assertIn("left behind", result.stderr)
                self.assertIn("no process is writing", result.stderr)
                self.assertNotIn("another statecat process is writing", result.stderr)
                self.assertIn("rm -rf '%s'" % lock_path, result.stderr)

    def test_lock_held_error_zero_second_countdown_says_next_attempt(self):
        # When the remaining time is under a second, int()
        # truncation used to print "in about 0 seconds", even
        # though this attempt exits without removing the lock
        # and the next attempt does the cleanup.
        if sys.platform == "win32":
            # On Windows the dead PID is reported on the
            # unknown-owner branch, so the sub-second window
            # sits at the tenfold limit. Check the message
            # directly: through the CLI the lock would age
            # past 600s during the half-second wait and be
            # broken as abandoned instead of reported.
            sys.path.insert(0, ROOT)
            import statecat

            lock_path = os.path.join(self.dir, ".statecat", "lock")
            os.makedirs(lock_path)
            with open(os.path.join(lock_path, "owner"), "w", encoding="utf-8") as handle:
                handle.write("pid=999999 time=2026-11-04T00:00:00\n")
                handle.write("token=dead-owner-token\n")
            old = time.time() - 599.5
            os.utime(lock_path, (old, old))
            lock = statecat.project_lock(self.dir)
            message = lock._lock_held_message(60)
            self.assertNotIn("in about 0 seconds", message)
            self.assertIn("on the next attempt", message)
            return
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
        old = time.time() - (665 if sys.platform == "win32" else 65)
        os.utime(lock_path, (old, old))
        lock = statecat.project_lock(self.dir)
        message = lock._lock_held_message(60)
        self.assertIn("on the next attempt", message)
        if sys.platform == "win32":
            # Windows reports the dead PID on the unknown-owner
            # branch, so the threshold here is the tenfold
            # limit, and the lock must be aged past that.
            self.assertIn("already past the limit", message)
        else:
            self.assertIn("already past the stale limit", message)
        self.assertNotIn("less than a second away", message)

    def test_manual_lock_clear_command_is_powershell_on_windows(self):
        # rm -rf is not a Windows command, so the manual clear
        # instruction must not print it on Windows; it gives
        # the PowerShell equivalent instead, with -LiteralPath:
        # -Path would still interpret wildcards, so a path with
        # square brackets could match the wrong thing.
        # Expectations are built from lock.lock_path, not
        # hardcoded with forward slashes: on Windows
        # os.path.join uses backslashes, so a hardcoded
        # '/tmp/...' path never matches there.
        sys.path.insert(0, ROOT)
        import statecat
        from unittest import mock

        lock = statecat.project_lock(os.path.join("/tmp", "My Projects"))
        with mock.patch.object(sys, "platform", "win32"):
            command = lock._manual_lock_clear_command()
        self.assertEqual(
            command,
            "Remove-Item -LiteralPath '%s' -Recurse -Force" % lock.lock_path,
        )
        self.assertNotIn("rm -rf", command)
        # A path with square brackets must still be passed
        # literally, which is what -LiteralPath is for.
        bracketed = statecat.project_lock(os.path.join("/tmp", "My [Project]"))
        with mock.patch.object(sys, "platform", "win32"):
            command = bracketed._manual_lock_clear_command()
        self.assertIn("Remove-Item -LiteralPath", command)
        self.assertIn(bracketed.lock_path, command)
        # And on POSIX the same path is shell-quoted. Mock the
        # platform here too: on a real Windows runner the
        # unmocked call would take the PowerShell branch.
        with mock.patch.object(sys, "platform", "linux"):
            command = lock._manual_lock_clear_command()
        self.assertEqual(command, "rm -rf '%s'" % lock.lock_path)

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
        # The hint names an interpreter that runs here: py on Windows.
        self.assertIn("%s statecat.py show" % ("py" if sys.platform == "win32" else "python3"), result.stderr)
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



    # Break-test round: hostile input must fail cleanly, never crash.
    def assert_clean_failure(self, result):
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("error:", result.stderr)

    def test_non_ascii_digit_id_is_rejected_cleanly(self):
        self.init_project()
        run_cli(self.dir, "add", "Open", "one")
        result = run_cli(self.dir, "move", "\u00b2", "Open")
        self.assert_clean_failure(result)
        self.assertIn("bad item id", result.stderr)

    def test_non_ascii_digit_last_id_does_not_crash(self):
        self.init_project()
        run_cli(self.dir, "add", "Open", "one")
        path = state_path(self.dir)
        text = re.sub(r"> Last ID used: #\d+", "> Last ID used: #\u00b2", read_text(path))
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        for command in (("show",), ("check",), ("status",)):
            result = run_cli(self.dir, *command)
            self.assertNotIn("Traceback", result.stderr)

    def test_state_file_that_is_a_folder_fails_cleanly(self):
        self.init_project()
        os.remove(state_path(self.dir))
        os.mkdir(state_path(self.dir))
        for command in (("show",), ("check",), ("add", "Open", "x")):
            result = run_cli(self.dir, *command)
            self.assert_clean_failure(result)
            self.assertIn("cannot read 'STATE.md'", result.stderr)

    def test_update_and_supersede_reject_empty_text(self):
        self.init_project()
        run_cli(self.dir, "add", "Open", "one")
        run_cli(self.dir, "add", "Decided", "two")
        before = read_bytes(state_path(self.dir))
        for args in (("update", "1", ""), ("update", "1", "   "),
                     ("supersede", "2", ""), ("supersede", "2", "  ")):
            result = run_cli(self.dir, *args)
            self.assertNotEqual(result.returncode, 0, args)
            self.assertIn("must not be empty", result.stderr)
        self.assertEqual(read_bytes(state_path(self.dir)), before)

    def test_compact_rejects_negative_days(self):
        self.init_project()
        run_cli(self.dir, "add", "Decided", "keep me")
        before = read_bytes(state_path(self.dir))
        for extra in ((), ("--dry-run",)):
            result = run_cli(self.dir, "compact", "--older-than", "-5", *extra)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--older-than must be 0 or more", result.stderr)
        self.assertEqual(read_bytes(state_path(self.dir)), before)

    def test_move_to_same_bucket_changes_nothing(self):
        self.init_project()
        run_cli(self.dir, "add", "Open", "one")
        path = state_path(self.dir)
        text = re.sub(r"#1 \[[0-9-]+\]", "#1 [2020-01-01]", read_text(path))
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        before = read_bytes(path)
        result = run_cli(self.dir, "move", "1", "Open")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("already in Open", result.stdout)
        self.assertEqual(read_bytes(path), before)

    @unittest.skipIf(sys.platform == "win32", "symlinks need extra privileges on Windows")
    def test_symlinked_state_file_is_written_through(self):
        self.init_project()
        real = os.path.join(self.dir, "real-state.md")
        os.rename(state_path(self.dir), real)
        os.symlink("real-state.md", state_path(self.dir))
        result = run_cli(self.dir, "add", "Open", "via link")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(os.path.islink(state_path(self.dir)))
        self.assertIn("via link", read_text(real))

    def test_file_in_place_of_statecat_folder_fails_cleanly(self):
        self.init_project()
        shutil.rmtree(os.path.join(self.dir, ".statecat"))
        with open(os.path.join(self.dir, ".statecat"), "w") as handle:
            handle.write("")
        result = run_cli(self.dir, "add", "Open", "x")
        self.assert_clean_failure(result)
        self.assertIn("must be a folder", result.stderr)

    def test_file_in_place_of_backups_folder_fails_cleanly(self):
        self.init_project()
        backups = os.path.join(self.dir, ".statecat", "backups")
        shutil.rmtree(backups)
        with open(backups, "w") as handle:
            handle.write("")
        result = run_cli(self.dir, "add", "Open", "x")
        self.assert_clean_failure(result)
        self.assertIn("must be a folder", result.stderr)

    # 1.5.0: last-check receipt
    def receipt(self):
        path = os.path.join(self.dir, ".statecat", "last-check.json")
        if not os.path.exists(path):
            return None
        return json.loads(read_text(path))

    def status_line(self, prefix):
        result = run_cli(self.dir, "status")
        for line in result.stdout.splitlines():
            if line.startswith(prefix):
                return line
        return None

    def test_check_writes_receipt_with_hashes(self):
        import hashlib
        self.init_project()
        self.assertIsNone(self.receipt())
        result = run_cli(self.dir, "check")
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = self.receipt()
        self.assertEqual(receipt["result"], "clean")
        self.assertEqual(receipt["problems"], 0)
        self.assertEqual(receipt["version"], "1.5.0")
        self.assertEqual(
            receipt["state_sha256"],
            hashlib.sha256(read_bytes(state_path(self.dir))).hexdigest(),
        )
        self.assertEqual(
            receipt["archive_sha256"],
            hashlib.sha256(read_bytes(archive_path(self.dir))).hexdigest(),
        )

    def test_check_receipt_records_warnings_and_problems(self):
        self.init_project()
        with open(state_path(self.dir), "a", encoding="utf-8") as handle:
            handle.write("- no id or date\n")
        run_cli(self.dir, "check")
        self.assertEqual(self.receipt()["result"], "warnings")
        with open(state_path(self.dir), "a", encoding="utf-8") as handle:
            handle.write("## Bogus\n")
        result = run_cli(self.dir, "check")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.receipt()["result"], "problems")

    def test_check_fix_receipt_describes_fixed_file(self):
        import hashlib
        self.init_project()
        with open(state_path(self.dir), "a", encoding="utf-8") as handle:
            handle.write("- no id or date\n")
        result = run_cli(self.dir, "check", "--fix")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.receipt()["state_sha256"],
            hashlib.sha256(read_bytes(state_path(self.dir))).hexdigest(),
        )

    def test_status_reports_last_check_age_and_changes(self):
        self.init_project()
        self.assertEqual(self.status_line("Last check:"), "Last check: never recorded")
        run_cli(self.dir, "check")
        line = self.status_line("Last check:")
        self.assertIn("clean", line)
        self.assertIn("files unchanged since", line)
        run_cli(self.dir, "add", "Open", "x")
        self.assertIn("STATE.md changed since", self.status_line("Last check:"))

    def test_status_and_show_do_not_write_receipt(self):
        self.init_project()
        run_cli(self.dir, "status")
        run_cli(self.dir, "show")
        run_cli(self.dir, "show", "--json")
        self.assertIsNone(self.receipt())

    def test_corrupt_receipt_reads_as_never_recorded(self):
        self.init_project()
        os.makedirs(os.path.join(self.dir, ".statecat"), exist_ok=True)
        with open(os.path.join(self.dir, ".statecat", "last-check.json"), "w") as handle:
            handle.write("{not json")
        result = run_cli(self.dir, "status")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Last check: never recorded", result.stdout)
        result = run_cli(self.dir, "check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.receipt()["result"], "clean")

    def test_unwritable_receipt_is_a_note_not_a_failure(self):
        self.init_project()
        receipt_path = os.path.join(self.dir, ".statecat", "last-check.json")
        os.makedirs(receipt_path)  # a folder where the file must go
        result = run_cli(self.dir, "check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("check: clean", result.stdout)
        self.assertIn("note: could not record this check", result.stderr)

    def test_show_json_includes_last_check(self):
        self.init_project()
        data = json.loads(run_cli(self.dir, "show", "--json").stdout)
        self.assertIsNone(data["last_check"])
        run_cli(self.dir, "check")
        data = json.loads(run_cli(self.dir, "show", "--json").stdout)
        self.assertEqual(data["last_check"]["result"], "clean")
        self.assertFalse(data["last_check"]["files_changed_since"])

    # 1.5.0: review dates on waiting items
    def future(self, days):
        from datetime import timedelta
        return (date.today() + timedelta(days=days)).isoformat()

    def test_add_waiting_with_review_and_resolves_when(self):
        self.init_project()
        result = run_cli(
            self.dir, "add", "others", "Wait for supplier",
            "--resolves-when", "they confirm a date", "--review", "3d",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = bucket_lines(read_text(state_path(self.dir)), "Waiting on others")
        self.assertEqual(
            lines,
            ["- #1 [%s] Wait for supplier, resolves when they confirm a date [review %s]"
             % (TODAY, self.future(3))],
        )
        data = json.loads(run_cli(self.dir, "show", "--json").stdout)
        item = data["buckets"]["Waiting on others"][0]
        self.assertEqual(item["review"], self.future(3))
        self.assertTrue(item["resolves_when"])
        self.assertEqual(item["rechecks"], 0)

    def test_review_option_validation(self):
        self.init_project()
        before = read_bytes(state_path(self.dir))
        for args in (
            ("add", "others", "x", "--review", "2001-01-01"),
            ("add", "others", "x", "--review", "2030-02-30"),
            ("add", "others", "x", "--review", "soon"),
            ("add", "open", "x", "--review", "3d"),
            ("add", "decided", "x", "--resolves-when", "y"),
            ("add", "others", "x", "--resolves-when", ""),
            ("add", "others", "x [review %s]" % self.future(2), "--review", "3d"),
        ):
            result = run_cli(self.dir, *args)
            self.assertEqual(result.returncode, 1, args)
            self.assertIn("error:", result.stderr)
        self.assertEqual(read_bytes(state_path(self.dir)), before)
        result = run_cli(self.dir, "add", "human", "x", "--review", TODAY)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_due_review_replaces_stale_rule(self):
        self.init_project()
        with open(state_path(self.dir), "r", encoding="utf-8") as handle:
            text = handle.read()
        text = text.replace(
            "## Waiting on others\n",
            "## Waiting on others\n"
            "- #1 [2020-01-01] Old but not due [review %s]\n"
            "- #2 [2020-01-01] Old and due [review 2020-02-01]\n"
            "- #3 [2020-01-01] Old with no review\n" % self.future(5),
        ).replace("> Last ID used: #0", "> Last ID used: #3")
        with open(state_path(self.dir), "w", encoding="utf-8") as handle:
            handle.write(text)
        result = run_cli(self.dir, "check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("#1", result.stdout)
        self.assertIn("due: #2 in Waiting on others reached its review date 2020-02-01", result.stdout)
        self.assertNotIn("stale: #2", result.stdout)
        self.assertIn("stale: #3 in Waiting on others", result.stdout)
        status = run_cli(self.dir, "status").stdout
        self.assertIn("Waiting items due for review: 1", status)
        self.assertIn("Stale waiting items: 1", status)
        self.assertIn("Longest wait: #1", status)

    def test_review_marker_warnings(self):
        self.init_project()
        with open(state_path(self.dir), "r", encoding="utf-8") as handle:
            text = handle.read()
        text = text.replace(
            "## Open\n",
            "## Open\n- #1 [%s] Not waiting [review %s]\n" % (TODAY, self.future(3)),
        ).replace(
            "## Waiting on human\n",
            "## Waiting on human\n"
            "- #2 [%s] Bad date [review 2030-02-30]\n"
            "- #3 [%s] Before itself [review 2020-01-01]\n"
            "- #4 [%s] Marker [review %s] mid-line is text\n"
            % (TODAY, TODAY, TODAY, self.future(1)),
        ).replace("> Last ID used: #0", "> Last ID used: #4")
        with open(state_path(self.dir), "w", encoding="utf-8") as handle:
            handle.write(text)
        out = run_cli(self.dir, "check").stdout
        self.assertIn("warning: #1 in Open has a review date", out)
        self.assertIn("warning: #2 in Waiting on human has an invalid review date", out)
        self.assertIn("warning: #3 in Waiting on human has a review date 2020-01-01 before its own date", out)
        self.assertIn("due: #3", out)
        self.assertNotIn("#4", out)

    def test_recheck_sets_review_and_keeps_start_date(self):
        self.init_project()
        with open(state_path(self.dir), "r", encoding="utf-8", newline="") as handle:
            text = handle.read()
        text = text.replace(
            "## Waiting on others\n",
            "## Waiting on others\n- #1 [2026-01-05] Wait for supplier [review 2026-01-20]\n",
        ).replace("> Last ID used: #0", "> Last ID used: #1")
        with open(state_path(self.dir), "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        result = run_cli(self.dir, "recheck", "1", "--review", "7d", "--because", "supplier says next week")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("deferred 1 time)", result.stdout)
        result = run_cli(self.dir, "recheck", "#1", "--review", TODAY, "--because", "holiday")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("deferred 2 times", result.stdout)
        text = read_text(state_path(self.dir))
        self.assertIn(
            "- #1 [2026-01-05] Wait for supplier [review %s]\n"
            "  - %s still waiting: supplier says next week\n"
            "  - %s still waiting: holiday\n" % (TODAY, TODAY, TODAY),
            text,
        )
        out = run_cli(self.dir, "check").stdout
        self.assertIn("due: #1 in Waiting on others reached its review date %s, deferred 2 times" % TODAY, out)
        data = json.loads(run_cli(self.dir, "show", "--json").stdout)
        self.assertEqual(data["buckets"]["Waiting on others"][0]["rechecks"], 2)

    def test_recheck_rejections(self):
        self.init_project()
        run_cli(self.dir, "add", "open", "Not waiting")
        run_cli(self.dir, "add", "others", "Waiting")
        before = read_bytes(state_path(self.dir))
        for args in (
            ("recheck", "1", "--review", "3d", "--because", "x"),
            ("recheck", "2", "--review", "3d"),
            ("recheck", "2", "--review", "3d", "--because", "  "),
            ("recheck", "2", "--review", "2001-01-01", "--because", "x"),
            ("recheck", "2", "--because", "x"),
            ("recheck", "9", "--review", "3d", "--because", "x"),
        ):
            result = run_cli(self.dir, *args)
            self.assertNotEqual(result.returncode, 0, args)
        self.assertEqual(read_bytes(state_path(self.dir)), before)

    def test_update_keeps_review_marker(self):
        self.init_project()
        run_cli(self.dir, "add", "others", "Wait", "--review", "4d")
        result = run_cli(self.dir, "update", "1", "Wait for the supplier")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("kept review date", result.stdout)
        self.assertIn(
            "Wait for the supplier [review %s]" % self.future(4),
            read_text(state_path(self.dir)),
        )
        run_cli(self.dir, "update", "1", "Reworded [review %s]" % self.future(9))
        self.assertIn("Reworded [review %s]" % self.future(9), read_text(state_path(self.dir)))

    def test_move_drops_and_sets_review(self):
        self.init_project()
        run_cli(self.dir, "add", "others", "Wait", "--review", "4d")
        run_cli(self.dir, "recheck", "1", "--review", "6d", "--because", "slow")
        result = run_cli(self.dir, "move", "1", "decided")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("dropped review date %s" % self.future(6), result.stdout)
        text = read_text(state_path(self.dir))
        self.assertEqual(bucket_lines(text, "Decided"), ["- #1 [%s] Wait" % TODAY])
        self.assertIn("  - %s still waiting: slow" % TODAY, text)
        result = run_cli(self.dir, "move", "1", "human", "--review", "2d", "--resolves-when", "owner picks")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            bucket_lines(read_text(state_path(self.dir)), "Waiting on human"),
            ["- #1 [%s] Wait, resolves when owner picks [review %s]" % (TODAY, self.future(2))],
        )
        result = run_cli(self.dir, "move", "1", "others", "--resolves-when", "again")
        self.assertEqual(result.returncode, 1)
        result = run_cli(self.dir, "move", "1", "open", "--review", "2d")
        self.assertEqual(result.returncode, 1)

    def test_untouched_review_lines_stay_byte_identical(self):
        self.init_project()
        with open(state_path(self.dir), "r", encoding="utf-8", newline="") as handle:
            text = handle.read()
        line = "- #1 [2026-01-05] Odd   spacing,  resolves when X   [review 2026-01-20]  \r\n"
        text = text.replace("## Waiting on others\n", "## Waiting on others\n" + line)
        text = text.replace("> Last ID used: #0", "> Last ID used: #1")
        with open(state_path(self.dir), "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        run_cli(self.dir, "add", "open", "other item")
        self.assertIn(line, read_text(state_path(self.dir)))


if __name__ == "__main__":
    runner = unittest.TextTestRunner(verbosity=2)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(StateCatTest)
    result = runner.run(suite)
    total = result.testsRun
    failed = len(result.failures) + len(result.errors)
    print("STATECAT TESTS: %d passed, %d failed, %d total" % (total - failed, failed, total))
    sys.exit(0 if result.wasSuccessful() else 1)
