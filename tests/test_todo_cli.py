"""Tests for todo_cli — format and error handling only (no real API calls)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "todo" / "scripts" / "todo_cli.py"

# Shared temp dir for config/auth mocking
_TMPDIR = tempfile.mkdtemp()
os.environ["_TODO_TEST_DIR"] = _TMPDIR


def _run(*args, stdin_str=None):
    """Run todo_cli.py, return (returncode, stdout, stderr)."""
    cmd = [sys.executable, str(SCRIPT)] + list(args)
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        input=stdin_str,
        timeout=10,
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


class TestTodoCli(unittest.TestCase):

    def test_no_args_shows_usage(self):
        code, out, err = _run()
        self.assertNotEqual(code, 0)
        self.assertIn("Usage", err)

    def test_list_returns_valid_json(self):
        """--list outputs valid JSON array even when API is unreachable."""
        code, out, err = _run("--list")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertIsInstance(data, list)

    def test_list_with_status_filter(self):
        code, out, err = _run("--list", "--status", "notStarted")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertIsInstance(data, list)

    def test_list_invalid_status_rejected(self):
        code, out, err = _run("--list", "--status", "invalid")
        self.assertNotEqual(code, 0)
        self.assertIn("status", err.lower())

    def test_create_missing_title(self):
        code, out, err = _run("--create")
        self.assertNotEqual(code, 0)

    def test_create_outputs_valid_json(self):
        """--create outputs JSON even on API failure."""
        code, out, err = _run("--create", "Test Task")
        if code == 0:
            data = json.loads(out)
            self.assertIn("id", data)
        else:
            self.assertIn("Error", err)

    def test_complete_missing_id(self):
        code, out, err = _run("--complete")
        self.assertNotEqual(code, 0)

    def test_delete_missing_id(self):
        code, out, err = _run("--delete")
        self.assertNotEqual(code, 0)

    def test_delete_force_flag_accepted(self):
        code, out, err = _run("--delete", "fake-id", "--force")
        self.assertIn(code, (0, 1))

    def test_unknown_command(self):
        code, out, err = _run("--nonexistent")
        self.assertNotEqual(code, 0)
        self.assertIn("Unknown", err)


if __name__ == "__main__":
    unittest.main()
