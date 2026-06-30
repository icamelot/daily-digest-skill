"""Delete completed To Do tasks older than N days."""
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from graph_api import get_tasks, delete_task


def _load_config() -> dict:
    config_path = Path(__file__).resolve().parent.parent.parent / "config.json"
    with open(config_path) as f:
        return json.load(f)


def cleanup_old_completed(days: int = 7, dry_run: bool = False) -> dict:
    """Delete completed tasks older than *days*.

    Returns summary dict with counts and deleted IDs.
    """
    config = _load_config()
    tasks = get_tasks(config)

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    to_delete: list[dict] = []

    for task in tasks:
        if task["status"] != "completed":
            continue
        completed_str = task.get("completed_date", "")
        if not completed_str:
            continue
        try:
            # Parse ISO 8601 — handles "2022-01-15T00:00:00.0000000" and "2022-01-15T00:00:00Z"
            completed_str = completed_str.replace("Z", "+00:00")
            # Handle the 7-digit fractional seconds Microsoft Graph sometimes uses
            if "." in completed_str and "+" in completed_str:
                base, frac_tz = completed_str.rsplit(".", 1)
                frac, tz = frac_tz.split("+", 1) if "+" in frac_tz else frac_tz.split("-", 1)
                frac = frac[:6]  # trim to microseconds
                completed_str = f"{base}.{frac}+{tz}"
            completed_dt = datetime.fromisoformat(completed_str)
            if completed_dt.tzinfo is None:
                completed_dt = completed_dt.replace(tzinfo=timezone.utc)
            if completed_dt < cutoff:
                to_delete.append(task)
        except (ValueError, IndexError):
            continue

    deleted = 0
    failed = 0
    for task in to_delete:
        if dry_run:
            deleted += 1
        else:
            if delete_task(config, task["id"]):
                deleted += 1
            else:
                failed += 1

    return {
        "total_completed": len([t for t in tasks if t["status"] == "completed"]),
        "older_than_cutoff": len(to_delete),
        "deleted": deleted,
        "failed": failed,
        "cutoff_date": cutoff.strftime("%Y-%m-%d"),
        "dry_run": dry_run,
    }


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Delete old completed To Do tasks")
    ap.add_argument("--days", type=int, default=7, help="Delete tasks completed > DAYS ago (default: 7)")
    ap.add_argument("--dry-run", action="store_true", help="Preview only, don't delete")
    args = ap.parse_args()

    result = cleanup_old_completed(days=args.days, dry_run=args.dry_run)
    print(json.dumps(result, indent=2, ensure_ascii=False))
