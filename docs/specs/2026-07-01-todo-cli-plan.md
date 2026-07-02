# Todo CLI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wrap `graph_api.py` in a self-contained CLI (`todo_cli.py`) that works from any directory.

**Architecture:** Single `todo_cli.py` script resolves its own paths via `__file__`, loads config/auth internally, delegates to `graph_api.py` functions, outputs JSON to stdout.

**Tech Stack:** Python 3 stdlib only. Reuses `graph_api.py` unchanged.

## Global Constraints

- Works from any cwd (uses `Path(__file__).resolve()` for all path resolution)
- stdout: JSON only. stderr: errors + auth prompts
- Exit code 0 = success, 1 = error
- `--list` returns `[]` on API failure, never crashes
- Same unittest + subprocess test pattern as `test_unprocessed_mail.py`

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `todo/scripts/todo_cli.py` | CREATE | Self-contained CLI, wraps graph_api.py |
| `todo/SKILL.md` | MODIFY | Replace python -c snippets with CLI commands |
| `tests/test_todo_cli.py` | CREATE | Tests for CLI output format + error handling |

---

### Task 1: Create `todo_cli.py` + tests

**Files:**
- Create: `skills/personal-assistant/todo/scripts/todo_cli.py`
- Create: `skills/personal-assistant/tests/test_todo_cli.py`

**Interfaces:**
- Produces:
  - `todo_cli.py --list [--status notStarted|completed]` → JSON array
  - `todo_cli.py --create "title" [--due "YYYY-MM-DD"] [--priority high|normal|low]` → JSON object
  - `todo_cli.py --complete <task-id>` → `{"ok": true}`
  - `todo_cli.py --delete <task-id> [--force]` → `{"ok": true}`
- Consumes: `graph_api.py` (get_tasks, create_task, update_task, delete_task)

- [ ] **Step 1: Write the test file**

File: `skills/personal-assistant/tests/test_todo_cli.py`

```python
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
        # Will fail because no real MS token, but should still output valid JSON
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
        # Will fail without real token, but output parses
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
        # Accepts the args even if API fails
        self.assertIn(code, (0, 1))

    def test_unknown_command(self):
        code, out, err = _run("--nonexistent")
        self.assertNotEqual(code, 0)
        self.assertIn("Unknown", err)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
bash -c 'cd /ductor/workspace/skills/personal-assistant && python3 -m unittest tests.test_todo_cli -v'
```
Expected: all FAIL (script doesn't exist)

- [ ] **Step 3: Create `todo_cli.py`**

File: `skills/personal-assistant/todo/scripts/todo_cli.py`

```python
#!/usr/bin/env python3
"""Todo CLI — self-contained wrapper for Microsoft To Do via Graph API.
Works from any directory. Outputs JSON to stdout, errors to stderr."""
import json
import os
import sys
from pathlib import Path

# Resolve skill root from this file's location: todo/scripts/todo_cli.py → skill root
SKILL_DIR = Path(__file__).resolve().parent.parent.parent
TODO_DIR = SKILL_DIR / "todo"
SCRIPTS_DIR = TODO_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from graph_api import get_tasks, create_task, update_task, delete_task

CONFIG_PATH = SKILL_DIR / "config.json"
TOKEN_FILE = TODO_DIR / ".ms_token.json"

# Allow test override
_TEST_DIR = os.environ.get("_TODO_TEST_DIR", "")
if _TEST_DIR:
    SKILL_DIR = Path(_TEST_DIR)
    CONFIG_PATH = SKILL_DIR / "config.json"
    TOKEN_FILE = SKILL_DIR / ".ms_token.json"


def _load_config() -> dict:
    """Load and return config with passwords resolved from env."""
    if not CONFIG_PATH.exists():
        print("Error: config.json not found", file=sys.stderr)
        sys.exit(1)
    with open(CONFIG_PATH) as f:
        config = json.load(f)

    # Resolve password_env → password
    for account in config.get("mail", {}).get("accounts", []):
        for key in ("imap", "smtp"):
            cfg = account.get(key, {})
            if "password_env" in cfg and "password" not in cfg:
                cfg["password"] = os.environ.get(cfg["password_env"], "")

    # Resolve MS Graph env vars
    ms_cfg = config.get("todo", {}).get("microsoft_graph", {})
    for key in ("client_id", "client_secret", "tenant_id"):
        env_key = f"MS_{key.upper()}"
        if not ms_cfg.get(key) and os.environ.get(env_key):
            ms_cfg[key] = os.environ[env_key]

    return config


def cmd_list(status: str | None = None):
    """List tasks, optionally filtered by status."""
    try:
        config = _load_config()
        tasks = get_tasks(config)
        if status:
            tasks = [t for t in tasks if t.get("status") == status]
        print(json.dumps(tasks, ensure_ascii=False))
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        # Return empty list on failure (never crash)
        print("[]")


def cmd_create(title: str, due: str | None = None, priority: str | None = None):
    """Create a new task."""
    try:
        config = _load_config()
        due_date = f"{due}T16:00:00.0000000" if due else None
        result = create_task(config, title, due_date, priority)
        if result:
            print(json.dumps(result, ensure_ascii=False))
        else:
            print("Error: failed to create task", file=sys.stderr)
            sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_complete(task_id: str):
    """Mark a task as completed."""
    try:
        config = _load_config()
        ok = update_task(config, task_id, {"status": "completed"})
        if ok:
            print(json.dumps({"ok": True}))
        else:
            print(json.dumps({"ok": False, "error": "update failed"}))
            sys.exit(1)
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        sys.exit(1)


def cmd_delete(task_id: str, force: bool = False):
    """Delete a task. Requires --force for safety."""
    if not force:
        print("Error: --delete requires --force for confirmation", file=sys.stderr)
        sys.exit(1)
    try:
        config = _load_config()
        ok = delete_task(config, task_id)
        if ok:
            print(json.dumps({"ok": True}))
        else:
            print(json.dumps({"ok": False, "error": "delete failed"}))
            sys.exit(1)
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        sys.exit(1)


def main():
    if len(sys.argv) < 2:
        print("Usage: todo_cli.py --list|--create|--complete|--delete [args]",
              file=sys.stderr)
        sys.exit(1)

    cmd = sys.argv[1]
    args = sys.argv[2:]

    if cmd == "--list":
        status = None
        if "--status" in args:
            idx = args.index("--status")
            if idx + 1 < len(args):
                status = args[idx + 1]
                if status not in ("notStarted", "inProgress", "completed", "waitingOnOthers", "deferred"):
                    print(f"Error: invalid status '{status}'. Valid: notStarted, inProgress, completed, waitingOnOthers, deferred",
                          file=sys.stderr)
                    sys.exit(1)
        cmd_list(status)

    elif cmd == "--create":
        if not args:
            print("Error: --create requires a title", file=sys.stderr)
            sys.exit(1)
        title = args[0]
        due = None
        priority = None
        if "--due" in args:
            idx = args.index("--due")
            if idx + 1 < len(args):
                due = args[idx + 1]
        if "--priority" in args:
            idx = args.index("--priority")
            if idx + 1 < len(args):
                priority = args[idx + 1]
        cmd_create(title, due, priority)

    elif cmd == "--complete":
        if not args:
            print("Error: --complete requires a task ID", file=sys.stderr)
            sys.exit(1)
        cmd_complete(args[0])

    elif cmd == "--delete":
        if not args:
            print("Error: --delete requires a task ID", file=sys.stderr)
            sys.exit(1)
        force = "--force" in args
        cmd_delete(args[0], force)

    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        print("Usage: todo_cli.py --list|--create|--complete|--delete [args]",
              file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests — verify pass**

```bash
bash -c 'cd /ductor/workspace/skills/personal-assistant && python3 -m unittest tests.test_todo_cli -v'
```
Expected: All 10 tests PASS (CLI output format tests don't need real API)

- [ ] **Step 5: Manual smoke test with real API**

```bash
bash -c 'cd /ductor/workspace/skills/personal-assistant && python3 todo/scripts/todo_cli.py --list'
```
Expected: valid JSON array of current tasks

- [ ] **Step 6: Commit**

```bash
git add todo/scripts/todo_cli.py tests/test_todo_cli.py
git commit -m "feat: add todo_cli.py — self-contained CLI for MS To Do"
```

---

### Task 2: Update `todo/SKILL.md` to use CLI

**Files:**
- Modify: `skills/personal-assistant/todo/SKILL.md`

**Interfaces:**
- Consumes: `todo_cli.py` commands from Task 1

- [ ] **Step 1: Rewrite SKILL.md with CLI commands**

Replace the entire content:

```markdown
# Todo 模块

通过 Microsoft Graph API 管理用户的 Microsoft To Do 任务。

所有操作通过 CLI 完成，任何目录可运行：

```bash
python3 /ductor/workspace/skills/personal-assistant/todo/scripts/todo_cli.py <command>
```

## 查任务

用户说"待办/任务/还有什么没做"时：

```bash
python3 /ductor/workspace/skills/personal-assistant/todo/scripts/todo_cli.py --list
```

可选过滤：
- `--status notStarted` — 仅未开始
- `--status completed` — 仅已完成

输出：JSON 数组。按优先级 + 截止时间排列后展示给用户。

## 创建任务

用户说"加个任务/提醒我..."时：

1. 解析任务名、可选截止时间、可选优先级
2. 展示确认卡片
3. 用户确认后执行：

```bash
python3 /ductor/workspace/skills/personal-assistant/todo/scripts/todo_cli.py \
  --create "任务标题" --due "2026-07-05" --priority high
```

`--due` 格式：YYYY-MM-DD。`--priority`：high / normal / low。

## 完成/修改任务

- "标记 xx 完成"：

```bash
python3 /ductor/workspace/skills/personal-assistant/todo/scripts/todo_cli.py \
  --complete <task-id>
```

- "删除 xx"：先确认再执行

```bash
python3 /ductor/workspace/skills/personal-assistant/todo/scripts/todo_cli.py \
  --delete <task-id> --force
```

## 清理旧任务

用户说"清理已完成任务/删除超过一周的任务"时：

1. 先 dry-run 预览：`python3 todo/scripts/cleanup_old_completed.py --days 7 --dry-run`
2. 告知用户将要删除的数量，获得确认
3. 执行删除：`python3 todo/scripts/cleanup_old_completed.py --days 7`
4. 报告结果（删除数 + 失败数）

`--days N` 控制天数阈值，默认 7 天。

## 主动感知

当你在对话中检测到用户可能完成了某个任务时，主动询问：

"检测到你似乎完成了 'xxx'，要标记为完成吗？ [标记完成]"

触发信号：
- 邮件模块中发送草稿成功 + 存在关联任务
- 用户说"做完了/搞定了/提交了/发了"
- 群聊日报中识别到任务相关结论

## 输出格式

不手动换行，让平台自适应。
```

- [ ] **Step 2: Commit**

```bash
git add todo/SKILL.md
git commit -m "docs: update todo SKILL.md to use CLI commands"
```

---

### Task 3: End-to-end verification

- [ ] **Step 1: Run full test suite**

```bash
bash -c 'cd /ductor/workspace/skills/personal-assistant && python3 -m unittest discover -s tests -v'
```
Expected: all tests PASS (existing 18 + new 10)

- [ ] **Step 2: Test CLI from outside the skill directory**

```bash
cd /tmp && python3 /ductor/workspace/skills/personal-assistant/todo/scripts/todo_cli.py --list
```
Expected: valid JSON output (same as from within skill dir)

- [ ] **Step 3: Verify --create arg parsing**

```bash
cd /tmp && python3 /ductor/workspace/skills/personal-assistant/todo/scripts/todo_cli.py --create "Test from CLI" --due "2026-12-31" --priority low
```
Expected: creates task, returns JSON with task id

- [ ] **Step 4: Commit and push**

```bash
git status
git log --oneline -3
```

---

## Implementation Order

```
Task 1 (CLI + tests) → Task 2 (SKILL.md) → Task 3 (e2e verify)
```

All tasks sequential — Task 2 needs the CLI from Task 1.
