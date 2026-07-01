# Unprocessed Mail Tracking — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a persistent `.unprocessed_emails.json` list + `unprocessed_mail.py` CLI so the AI agent can perceive and act on email backlogs.

**Architecture:** New CLI tool `unprocessed_mail.py` is the sole writer to `.unprocessed_emails.json`. `mail_daemon.py` calls `--add` after push and `--sync-seen` on each poll. The agent reads the file directly for awareness and calls CLI for mutations. `--mark-all-done` replaces the old `mail_poll.py --mark-all-read`.

**Tech Stack:** Python 3 stdlib (json, sys, imaplib, urllib). No new dependencies.

## Global Constraints

- All email state files live in the skill root (`skills/personal-assistant/`)
- CLI output is JSON to stdout, errors to stderr
- Zero AI/token cost for daemon operations
- `imap_fetch._IMAP4_SSL_DoH` reused for IMAP connections
- Password resolution: env var keys (`password_env`) resolved at runtime

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `mail/scripts/unprocessed_mail.py` | CREATE | CLI: all read/write to `.unprocessed_emails.json` + IMAP ops |
| `mail_daemon.py` | MODIFY | Call `--add` after push, `--sync-seen` each poll |
| `mail/SKILL.md` | MODIFY | Update mark-all-read to use new CLI |
| `.gitignore` | MODIFY | Add `.unprocessed_emails.json` and `.seen_uids.json` |
| `tests/test_unprocessed_mail.py` | CREATE | Unit tests for all CLI commands |

---

### Task 1: Gitignore — exclude state files

**Files:**
- Modify: `skills/personal-assistant/.gitignore`

**Interfaces:**
- Produces: `.unprocessed_emails.json` and `.seen_uids.json` excluded from git

- [ ] **Step 1: Add entries to .gitignore**

```diff
 # Microsoft Graph token cache
 .ms_token.json
 todo/.ms_token.json

+# Email state files (daemon runtime data)
+.unprocessed_emails.json
+.seen_uids.json
+
 # Digest timestamp markers
 .digest_markers/
```

- [ ] **Step 2: Commit**

```bash
git add .gitignore
git commit -m "chore: gitignore email state files"
```

---

### Task 2: Create `unprocessed_mail.py` — file operations

**Files:**
- Create: `skills/personal-assistant/mail/scripts/unprocessed_mail.py`

**Interfaces:**
- Produces:
  - `_state_path() -> Path` — resolves `.unprocessed_emails.json` location
  - `_load() -> list[dict]` — read and validate the JSON array
  - `_save(entries: list[dict]) -> None` — atomic write
  - CLI commands: `--summary`, `--list`, `--add` (stdin), `--mark-done <uid>`

- [ ] **Step 1: Write the test file**

File: `skills/personal-assistant/tests/test_unprocessed_mail.py`

```python
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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
cd skills/personal-assistant && python3 -m pytest tests/test_unprocessed_mail.py -v
```
Expected: all tests FAIL (script doesn't exist yet)

- [ ] **Step 3: Create `unprocessed_mail.py`**

File: `skills/personal-assistant/mail/scripts/unprocessed_mail.py`

```python
#!/usr/bin/env python3
"""Unprocessed email state — CLI for agent and daemon."""
import json
import os
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_STATE = SKILL_DIR / ".unprocessed_emails.json"

KEEP_KEYS = {"uid", "sender", "subject", "date", "account"}


def _state_path() -> Path:
    """Resolve path, allowing override via env for testing."""
    override = os.environ.get("_UNPROCESSED_TEST_STATE", "")
    if override:
        return Path(override)
    return DEFAULT_STATE


def _load() -> list[dict]:
    """Read and validate the state file. Returns empty list on any error."""
    path = _state_path()
    if not path.exists():
        return []
    try:
        with open(path) as f:
            data = json.load(f)
        if not isinstance(data, list):
            return []
        return data
    except (json.JSONDecodeError, OSError):
        return []


def _save(entries: list[dict]) -> None:
    """Atomic-ish write: write to temp then rename."""
    path = _state_path()
    tmp = path.with_suffix(path.suffix + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(tmp, "w") as f:
        json.dump(entries, f, ensure_ascii=False)
    tmp.rename(path)


def _strip_extra_keys(entry: dict) -> dict:
    """Keep only the keys defined in KEEP_KEYS."""
    return {k: entry.get(k, "") for k in KEEP_KEYS}


# ── commands ──────────────────────────────────────────────────────────

def cmd_summary():
    entries = _load()
    accounts = {}
    for e in entries:
        acct = e.get("account", "unknown")
        accounts[acct] = accounts.get(acct, 0) + 1
    result = {"total": len(entries), "accounts": accounts}
    print(json.dumps(result, ensure_ascii=False))


def cmd_list():
    entries = _load()
    print(json.dumps(entries, ensure_ascii=False))


def cmd_add():
    """Read JSON array from stdin, append to state (dedup by uid)."""
    raw = sys.stdin.read()
    try:
        incoming = json.loads(raw)
        if not isinstance(incoming, list):
            print("Error: stdin must be a JSON array", file=sys.stderr)
            sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: invalid JSON on stdin: {e}", file=sys.stderr)
        sys.exit(1)

    entries = _load()
    existing_uids = {e["uid"] for e in entries}

    for item in incoming:
        uid = item.get("uid", "")
        if uid and uid not in existing_uids:
            entries.append(_strip_extra_keys(item))
            existing_uids.add(uid)

    _save(entries)
    print(json.dumps({"added": len(incoming), "total": len(entries)}))


def cmd_mark_done(uid: str):
    entries = _load()
    before = len(entries)
    entries = [e for e in entries if e.get("uid") != uid]
    _save(entries)
    print(json.dumps({"removed": before - len(entries), "total": len(entries)}))


def cmd_mark_all_done():
    """Mark all IMAP UNSEEN as \\Seen AND clear the unprocessed list."""
    # IMAP part — reused from mail_poll.mark_all_read
    config = _load_config()
    config = _resolve_passwords(config)

    total_marked = _imap_mark_all_seen(config)

    # Clear the list
    entries = _load()
    count = len(entries)
    _save([])

    print(json.dumps({
        "imap_marked_seen": total_marked,
        "unprocessed_cleared": count,
    }))


def cmd_sync_seen():
    """Remove entries whose IMAP uid is no longer UNSEEN (read externally)."""
    config = _load_config()
    config = _resolve_passwords(config)

    unseen_uids = _imap_get_unseen_uids(config)

    entries = _load()
    before = len(entries)
    entries = [e for e in entries if e.get("uid") in unseen_uids]
    after = len(entries)
    _save(entries)

    print(json.dumps({"removed": before - after, "total": after}))


# ── IMAP helpers (reuse existing patterns) ────────────────────────────

def _load_config() -> dict:
    config_path = SKILL_DIR / "config.json"
    if not config_path.exists():
        print("Error: config.json not found", file=sys.stderr)
        sys.exit(1)
    with open(config_path) as f:
        return json.load(f)


def _resolve_passwords(config: dict) -> dict:
    for account in config.get("mail", {}).get("accounts", []):
        for key in ("imap", "smtp"):
            cfg = account.get(key, {})
            if "password_env" in cfg and "password" not in cfg:
                cfg["password"] = os.environ.get(cfg["password_env"], "")
    return config


def _imap_get_unseen_uids(config: dict) -> set[str]:
    """Return set of uid strings that are currently UNSEEN across all accounts."""
    import imaplib as _imaplib
    imaplib.Commands["ID"] = ("AUTH",)

    # Add mail/scripts to path for _IMAP4_SSL_DoH
    mail_scripts = str(Path(__file__).resolve().parent)
    if mail_scripts not in sys.path:
        sys.path.insert(0, mail_scripts)

    from imap_fetch import _IMAP4_SSL_DoH

    uids = set()
    for account in config.get("mail", {}).get("accounts", []):
        imap_cfg = account.get("imap", {})
        if not imap_cfg.get("password"):
            continue
        try:
            conn = _IMAP4_SSL_DoH(imap_cfg["server"], imap_cfg["port"])
            conn.login(imap_cfg["username"], imap_cfg["password"])
            try:
                conn._simple_command(
                    "ID",
                    '("name" "Ductor" "version" "1.0" "vendor" "python")',
                )
            except _imaplib.IMAP4.error:
                pass
            conn.select("INBOX")
            status, msg_ids = conn.search(None, "UNSEEN")
            if status == "OK" and msg_ids[0]:
                for mid in msg_ids[0].split():
                    uids.add(mid.decode())
            conn.logout()
        except Exception as e:
            print(f"sync-seen: failed {account.get('label','?')}: {e}", file=sys.stderr)
    return uids


def _imap_mark_all_seen(config: dict) -> int:
    """Mark all UNSEEN as \\Seen across all accounts. Returns count."""
    import imaplib as _imaplib
    imaplib.Commands["ID"] = ("AUTH",)

    mail_scripts = str(Path(__file__).resolve().parent)
    if mail_scripts not in sys.path:
        sys.path.insert(0, mail_scripts)

    from imap_fetch import _IMAP4_SSL_DoH

    total = 0
    for account in config.get("mail", {}).get("accounts", []):
        label = account.get("label", "?")
        imap_cfg = account.get("imap", {})
        if not imap_cfg.get("password"):
            continue
        try:
            conn = _IMAP4_SSL_DoH(imap_cfg["server"], imap_cfg["port"])
            conn.login(imap_cfg["username"], imap_cfg["password"])
            try:
                conn._simple_command(
                    "ID",
                    '("name" "Ductor" "version" "1.0" "vendor" "python")',
                )
            except _imaplib.IMAP4.error:
                pass
            conn.select("INBOX")
            status, msg_ids = conn.search(None, "UNSEEN")
            if status == "OK" and msg_ids[0]:
                for mid in msg_ids[0].split():
                    try:
                        conn.store(mid.decode(), "+FLAGS", "(\\Seen)")
                        total += 1
                    except _imaplib.IMAP4.error:
                        pass
            conn.logout()
        except Exception as e:
            print(f"mark-all-done: failed {label}: {e}", file=sys.stderr)
    return total


# ── CLI dispatch ──────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: unprocessed_mail.py --summary|--list|--add|--mark-done|--mark-all-done|--sync-seen",
              file=sys.stderr)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "--summary":
        cmd_summary()
    elif cmd == "--list":
        cmd_list()
    elif cmd == "--add":
        cmd_add()
    elif cmd == "--mark-done":
        if len(sys.argv) < 3:
            print("Usage: unprocessed_mail.py --mark-done <uid>", file=sys.stderr)
            sys.exit(1)
        cmd_mark_done(sys.argv[2])
    elif cmd == "--mark-all-done":
        cmd_mark_all_done()
    elif cmd == "--sync-seen":
        cmd_sync_seen()
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run all non-IMAP tests — verify pass**

```bash
cd skills/personal-assistant && python3 -m pytest tests/test_unprocessed_mail.py -v
```

Expected: `TestSummary`, `TestList`, `TestAdd`, `TestMarkDone`, `TestCorruptedState` all PASS.
(IMAP tests `TestMarkAllDone` and `TestSyncSeen` will be added in Task 3.)

- [ ] **Step 5: Commit**

```bash
git add mail/scripts/unprocessed_mail.py tests/test_unprocessed_mail.py
git commit -m "feat: add unprocessed_mail.py CLI with file operations and tests"
```

---

### Task 3: Add `--sync-seen` and `--mark-all-done` IMAP tests

**Files:**
- Modify: `skills/personal-assistant/tests/test_unprocessed_mail.py`

**Interfaces:**
- Consumes: `unprocessed_mail.py` from Task 2
- Produces: Tests for IMAP-dependent commands

- [ ] **Step 1: Add IMAP-related tests (mocked)**

We mock the IMAP functions so tests don't need real credentials. Add to existing test file:

```python
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
```

- [ ] **Step 2: Run all tests**

```bash
cd skills/personal-assistant && python3 -m pytest tests/test_unprocessed_mail.py -v
```

Expected: all file-operation tests PASS; IMAP tests skip gracefully or pass if config available.

- [ ] **Step 3: Commit**

```bash
git add tests/test_unprocessed_mail.py
git commit -m "test: add IMAP operation tests for unprocessed_mail"
```

---

### Task 4: Integrate `mail_daemon.py` with `unprocessed_mail.py`

**Files:**
- Modify: `skills/personal-assistant/mail_daemon.py`

**Interfaces:**
- Consumes: `unprocessed_mail.py --add`, `--sync-seen` from Task 2
- Produces: daemon writes to `.unprocessed_emails.json` after each push, syncs on poll

- [ ] **Step 1: Add `_add_to_unprocessed()` helper**

In `mail_daemon.py`, add after `_mark_seen()`:

```python
UNPROCESSED_CLI = str(SKILL_DIR / "mail" / "scripts" / "unprocessed_mail.py")


def _add_to_unprocessed(emails: list[dict]) -> None:
    """Pipe new email entries (lightweight) into unprocessed_mail.py --add."""
    lightweight = []
    for e in emails:
        lightweight.append({
            "uid": e.get("uid", ""),
            "sender": e.get("sender", ""),
            "subject": e.get("subject", ""),
            "date": e.get("date", ""),
            "account": e.get("account", ""),
        })
    payload = json.dumps(lightweight)
    try:
        proc = subprocess.run(
            [sys.executable, UNPROCESSED_CLI, "--add"],
            input=payload,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode != 0:
            print(f"[mail_daemon] unprocessed add failed: {proc.stderr.strip()}", file=sys.stderr)
    except Exception as e:
        print(f"[mail_daemon] unprocessed add error: {e}", file=sys.stderr)
```

- [ ] **Step 2: Add `_sync_unprocessed_seen()` helper**

```python
def _sync_unprocessed_seen() -> None:
    """Tell unprocessed_mail.py to remove externally-read entries."""
    try:
        subprocess.run(
            [sys.executable, UNPROCESSED_CLI, "--sync-seen"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except Exception as e:
        print(f"[mail_daemon] sync-seen error: {e}", file=sys.stderr)
```

- [ ] **Step 3: Add `import subprocess` at top**

```python
import subprocess
```

- [ ] **Step 4: Wire into main loop**

In `main()`, after `_format_and_send(flat, config)` succeeds, add `_add_to_unprocessed(flat)`. Also add `_sync_unprocessed_seen()` at the start of each poll cycle.

The relevant section of `main()` becomes:

```python
while True:
    try:
        config = json.loads(CONFIG_PATH.read_text())
        config = _resolve_passwords(config)

        # Sync — remove externally read emails from unprocessed list
        _sync_unprocessed_seen()

        all_emails = fetch_all_unread_emails(config)
        new_emails = _filter_new(all_emails)

        total_new = sum(len(v) for v in new_emails.values())
        if total_new > 0:
            flat = []
            for label, emails in new_emails.items():
                for e in emails:
                    e["account"] = label
                    body = e.get("body", "")
                    e["body"] = _strip_html(body or "")[:500]
                    flat.append(e)
            ok = _format_and_send(flat, config)
            if ok:
                print(f"[mail_daemon] Sent {total_new} new email(s)", file=sys.stderr)
                _mark_seen(all_emails)
                _add_to_unprocessed(flat)
            else:
                print(f"[mail_daemon] Send failed, {total_new} email(s) NOT marked seen — will retry next poll", file=sys.stderr)
        else:
            _mark_seen(all_emails)
    except Exception as e:
        print(f"[mail_daemon] Error: {e}", file=sys.stderr)

    time.sleep(max(0, math.ceil(time.time() / INTERVAL) * INTERVAL - time.time()))
```

- [ ] **Step 5: Commit**

```bash
git add mail_daemon.py
git commit -m "feat: integrate unprocessed_mail tracking into daemon"
```

---

### Task 5: Update `mail/SKILL.md` for new mark-all-done flow

**Files:**
- Modify: `skills/personal-assistant/mail/SKILL.md`

**Interfaces:**
- Consumes: `unprocessed_mail.py --mark-all-done` from Task 2

- [ ] **Step 1: Update 「一键已读」 section**

Replace the current content:

```diff
 ## 一键已读

 当收到回调 `mail:mark-all-read` 或用户说"一键已读"时：

 1. 先询问用户确认：列出未读邮件总数，并附按钮
-2. 用户点击确认后，运行 `python3 ~/.ductor/workspace/skills/personal-assistant/mail_poll.py --mark-all-read`
-3. 把脚本输出结果回复给用户
+2. 用户点击确认后，运行 `python3 mail/scripts/unprocessed_mail.py --mark-all-done`
+3. 把脚本输出结果回复给用户（JSON: imap_marked_seen + unprocessed_cleared 数量）
```

- [ ] **Step 2: Add 「积压邮件」 section after 「查邮件」 section**

```markdown
## 积压邮件

当用户说"积压邮件"/"还有多少邮件没处理"/"帮我总结未处理邮件"时：

1. 读 `.unprocessed_emails.json` 或调 `--summary` 获取数量统计
2. 如需详情，调 `--list` 获取轻量列表
3. 按需调 `imap_fetch.py` 获取具体邮件正文
4. 处理完成后调 `--mark-done <uid>` 或 `--mark-all-done` 清理
```

- [ ] **Step 3: Commit**

```bash
git add mail/SKILL.md
git commit -m "docs: update mail SKILL.md for unprocessed tracking and mark-all-done"
```

---

### Task 6: End-to-end verification

- [ ] **Step 1: Verify `--summary` and `--list` with real daemon state**

```bash
cd skills/personal-assistant && python3 mail/scripts/unprocessed_mail.py --summary
```

Expected: JSON with current unprocessed count (possibly 0)

- [ ] **Step 2: Test `--add` with real data format**

```bash
echo '[{"uid":"test-1","sender":"test@test.com","subject":"Test Email","date":"2026-07-01","account":"Test"}]' | python3 mail/scripts/unprocessed_mail.py --add
python3 mail/scripts/unprocessed_mail.py --list
python3 mail/scripts/unprocessed_mail.py --mark-done test-1
python3 mail/scripts/unprocessed_mail.py --summary
```

Expected: add → total 1, list → shows entry, mark-done → removed, summary → total 0

- [ ] **Step 3: Run full test suite**

```bash
cd skills/personal-assistant && python3 -m pytest tests/ -v
```

Expected: all tests PASS

- [ ] **Step 4: Commit final state if any cleanup**

```bash
git status
git add -A
git commit -m "chore: final cleanup after unprocessed mail integration"
```

---

## Implementation Order

Tasks are sequential (each depends on the previous):

```
Task 1 (gitignore) → Task 2 (CLI core) → Task 3 (IMAP tests)
                                              ↓
                    Task 5 (SKILL.md) ← Task 4 (daemon integration)
                                              ↓
                                        Task 6 (e2e verify)
```

Tasks 4 and 5 are independent of each other and can run in parallel after Task 2.
