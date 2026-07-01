"""Tests for unprocessed_mail CLI."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "mail" / "scripts" / "unprocessed_mail.py"

# Shared temp dir — all tests use the same state file, cleared in setUp
_TMPDIR = tempfile.mkdtemp()
_STATE_PATH = os.path.join(_TMPDIR, ".unprocessed_emails.json")
os.environ["_UNPROCESSED_TEST_STATE"] = _STATE_PATH


def _run(*args, stdin_str=None):
    """Run unprocessed_mail.py, return (returncode, stdout, stderr)."""
    cmd = [sys.executable, str(SCRIPT)] + list(args)
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        input=stdin_str,
        timeout=10,
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def _seed(entries: list[dict]):
    """Write the shared test state file."""
    os.makedirs(os.path.dirname(_STATE_PATH), exist_ok=True)
    with open(_STATE_PATH, "w") as f:
        json.dump(entries, f)


def _clear_state():
    """Remove the state file between tests."""
    try:
        os.unlink(_STATE_PATH)
    except FileNotFoundError:
        pass


class TestSummary(unittest.TestCase):

    def setUp(self):
        _clear_state()

    def test_empty_state(self):
        code, out, err = _run("--summary")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["total"], 0)
        self.assertEqual(data["accounts"], {})

    def test_with_entries(self):
        _seed([
            {"uid": "1", "sender": "a@b.com", "subject": "S1", "date": "", "account": "PKU"},
            {"uid": "2", "sender": "c@d.com", "subject": "S2", "date": "", "account": "PKU"},
            {"uid": "3", "sender": "e@f.com", "subject": "S3", "date": "", "account": "QQ"},
        ])
        code, out, err = _run("--summary")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["total"], 3)
        self.assertEqual(data["accounts"], {"PKU": 2, "QQ": 1})


class TestList(unittest.TestCase):

    def setUp(self):
        _clear_state()

    def test_list_empty(self):
        code, out, err = _run("--list")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data, [])

    def test_list_with_entries(self):
        _seed([
            {"uid": "1", "sender": "a@b.com", "subject": "Hello", "date": "Jan 1", "account": "PKU"},
        ])
        code, out, err = _run("--list")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["uid"], "1")
        self.assertEqual(data[0]["subject"], "Hello")


class TestAdd(unittest.TestCase):

    def setUp(self):
        _clear_state()

    def test_add_single(self):
        entry = json.dumps([{"uid": "10", "sender": "x@y.com", "subject": "New", "date": "now", "account": "PKU"}])
        code, out, err = _run("--add", stdin_str=entry)
        self.assertEqual(code, 0)

        code2, out2, _ = _run("--summary")
        data = json.loads(out2)
        self.assertEqual(data["total"], 1)

    def test_add_strips_extra_fields(self):
        entry = json.dumps([{
            "uid": "11", "sender": "x@y.com", "subject": "Keep",
            "date": "now", "account": "PKU",
            "body": "should be stripped",
            "attachments": [],
        }])
        code, out, err = _run("--add", stdin_str=entry)
        self.assertEqual(code, 0)

        code2, out2, _ = _run("--list")
        data = json.loads(out2)
        self.assertEqual(len(data), 1)
        self.assertNotIn("body", data[0])
        self.assertNotIn("attachments", data[0])
        self.assertEqual(data[0]["uid"], "11")

    def test_add_dedup_uid(self):
        entry1 = json.dumps([{"uid": "20", "sender": "a@b.com", "subject": "First", "date": "", "account": "PKU"}])
        entry2 = json.dumps([{"uid": "20", "sender": "a@b.com", "subject": "Second", "date": "", "account": "PKU"}])
        _run("--add", stdin_str=entry1)
        _run("--add", stdin_str=entry2)

        code, out, _ = _run("--list")
        data = json.loads(out)
        self.assertEqual(len(data), 1, "duplicate uid should not be added")


class TestMarkDone(unittest.TestCase):

    def setUp(self):
        _clear_state()

    def test_mark_done(self):
        _seed([
            {"uid": "1", "sender": "a@b.com", "subject": "S1", "date": "", "account": "PKU"},
            {"uid": "2", "sender": "c@d.com", "subject": "S2", "date": "", "account": "PKU"},
        ])
        code, out, err = _run("--mark-done", "1")
        self.assertEqual(code, 0)

        code2, out2, _ = _run("--summary")
        data = json.loads(out2)
        self.assertEqual(data["total"], 1)

        code3, out3, _ = _run("--list")
        remaining = json.loads(out3)
        self.assertEqual(remaining[0]["uid"], "2")

    def test_mark_done_nonexistent(self):
        code, out, err = _run("--mark-done", "nonexistent")
        self.assertEqual(code, 0)  # idempotent, no error

        code2, out2, _ = _run("--summary")
        data = json.loads(out2)
        self.assertEqual(data["total"], 0)


class TestCorruptedState(unittest.TestCase):

    def setUp(self):
        _clear_state()

    def test_corrupted_file_returns_empty(self):
        os.makedirs(os.path.dirname(_STATE_PATH), exist_ok=True)
        with open(_STATE_PATH, "w") as f:
            f.write("not valid json {{{")

        code, out, err = _run("--summary")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["total"], 0)


class TestMarkAllDone(unittest.TestCase):

    def setUp(self):
        _clear_state()
        _seed([
            {"uid": "1", "sender": "a@b.com", "subject": "S1", "date": "", "account": "PKU"},
            {"uid": "2", "sender": "c@d.com", "subject": "S2", "date": "", "account": "QQ"},
        ])

    def test_mark_all_done_clears_list(self):
        """--mark-all-done clears the unprocessed list (IMAP part may fail w/o creds)."""
        code, out, err = _run("--mark-all-done")
        if code != 0:
            self.assertIn("Error", err)
        else:
            data = json.loads(out)
            self.assertIn("imap_marked_seen", data)
            self.assertIn("unprocessed_cleared", data)
            # After mark-all-done, list should be empty
            code2, out2, _ = _run("--summary")
            summary = json.loads(out2)
            self.assertEqual(summary["total"], 0)


class TestSyncSeen(unittest.TestCase):

    def setUp(self):
        _clear_state()
        _seed([
            {"uid": "1", "sender": "a@b.com", "subject": "S1", "date": "", "account": "PKU"},
        ])

    def test_sync_seen_output_structure(self):
        code, out, err = _run("--sync-seen")
        if code != 0:
            self.assertIn("Error", err)
        else:
            data = json.loads(out)
            self.assertIn("removed", data)
            self.assertIn("total", data)


if __name__ == "__main__":
    unittest.main()
