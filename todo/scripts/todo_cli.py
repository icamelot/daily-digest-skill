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


class ConfigError(Exception):
    """Raised when config is missing or invalid."""
    pass


def _load_config() -> dict:
    """Load and return config with passwords resolved from env.
    Raises ConfigError if config.json is missing."""
    if not CONFIG_PATH.exists():
        raise ConfigError("config.json not found")
    with open(CONFIG_PATH) as f:
        config = json.load(f)

    # Resolve MS Graph env vars (same logic as graph_api._resolve_ms_cfg)
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
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        print("[]")
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
                valid = ("notStarted", "inProgress", "completed", "waitingOnOthers", "deferred")
                if status not in valid:
                    print(f"Error: invalid status '{status}'. Valid: {', '.join(valid)}",
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
