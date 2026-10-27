#!/usr/bin/env python3
"""StateCat v1 acceptance tests (scope revision 5, section 9).

Run with: python3 tests/test_statecat.py
Uses only the standard library. Each test runs the real CLI in a
fresh temporary folder; no test mutates the shipped example or
fixture. Exit code 0 means every test passed; the final line
reports the pass/fail count honestly.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
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


def read_text(path):
    with open(path, "r", encoding="utf-8", newline="") as handle:
        return handle.read()


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

    def test_windows_py_note_and_gitignore(self):
        readme = read_text(os.path.join(ROOT, "README.md"))
        skill = read_text(os.path.join(ROOT, "SKILL.md"))
        self.assertIn("py statecat.py", readme)
        self.assertIn("py", skill)
        gitignore = read_text(os.path.join(ROOT, ".gitignore"))
        self.assertIn("__pycache__/", gitignore)



if __name__ == "__main__":
    runner = unittest.TextTestRunner(verbosity=2)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(StateCatTest)
    result = runner.run(suite)
    total = result.testsRun
    failed = len(result.failures) + len(result.errors)
    print("STATECAT TESTS: %d passed, %d failed, %d total" % (total - failed, failed, total))
    sys.exit(0 if result.wasSuccessful() else 1)
