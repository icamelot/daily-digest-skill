# Todo CLI — Robust CLI Wrapper for Microsoft To Do

**Date**: 2026-07-01
**Status**: design approved, awaiting plan

## Problem

`todo/scripts/graph_api.py` is a Python library, not a CLI. Calling it requires:
- `cd` to the skill root directory
- Manual `sys.path.insert`
- Hand-written Python one-liners with fragile shell quoting

Every invocation is a unique code snippet, brittle and error-prone.

## Solution

A single `todo_cli.py` that:
- Resolves its own paths via `__file__`, works from any cwd
- Handles config loading, auth, and formatting internally
- Outputs clean JSON to stdout, errors to stderr
- Agent calls become: `python3 <absolute-path> --list`

## Commands

```bash
# List all tasks (all lists, all statuses)
python3 todo/scripts/todo_cli.py --list

# List with optional status filter
python3 todo/scripts/todo_cli.py --list --status notStarted

# Create task
python3 todo/scripts/todo_cli.py --create "title" [--due "2026-07-05"] [--priority high|normal|low]

# Mark task complete
python3 todo/scripts/todo_cli.py --complete <task-id>

# Delete task (with confirmation)
python3 todo/scripts/todo_cli.py --delete <task-id> [--force]
```

## Output Contract

- stdout: JSON only (agent parses with `json.loads`)
- stderr: errors and warnings (auth prompts during device-code flow)
- Exit code: 0 = success, 1 = error
- `--list` returns `[]` on API failure (never crashes)

## Files

| File | Action | Purpose |
|------|--------|---------|
| `todo/scripts/todo_cli.py` | CREATE | Self-contained CLI wrapper |
| `todo/SKILL.md` | MODIFY | Update to use CLI commands |
| `tests/test_todo_cli.py` | CREATE | Tests for CLI output format and error handling |

## Non-goals

- Replacing or rewriting `graph_api.py` (it stays as-is, CLI wraps it)
- Adding new Graph API features
- Changing the MS token/auth flow
