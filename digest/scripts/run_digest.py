#!/usr/bin/env python3
"""Run digest data collection pipeline. Outputs JSON to stdout.

Usage:
    python3 run_digest.py --type morning
    python3 run_digest.py --type evening
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(SKILL_DIR / "shared"))
sys.path.insert(0, str(SKILL_DIR / "digest" / "scripts"))
sys.path.insert(0, str(SKILL_DIR / "mail" / "scripts"))
sys.path.insert(0, str(SKILL_DIR / "todo" / "scripts"))

from utils import load_env_secrets, resolve_config
from group_reader import (
    get_last_digest_timestamp,
    set_last_digest_timestamp,
    fetch_group_messages,
)
from escalate import check_escalation
from imap_fetch import fetch_all_unread_emails
from filter_rules import classify_emails
from deepseek_balance import load_snapshots, _parse_ts

BALANCE_SCRIPT = SKILL_DIR / "digest" / "scripts" / "deepseek_balance.py"
CONFIG_PATH = SKILL_DIR / "config.json"


def load_config():
    """Load and resolve config with secrets."""
    raw_config = json.loads(CONFIG_PATH.read_text())
    env_secrets = load_env_secrets()
    for key, val in env_secrets.items():
        if key not in os.environ:
            os.environ[key] = val
    return resolve_config(raw_config)


def compute_deepseek(snapshot_label: str, comparison_label: str, consumption_icon: str, consumption_label: str) -> dict | None:
    """Record a DeepSeek balance snapshot and compute consumption vs a prior snapshot.

    Args:
        snapshot_label: 'morning' or 'evening' — label for today's snapshot
        comparison_label: 'evening' (for morning digest — yesterday evening) or 'morning' (for evening digest — today morning)
        consumption_icon: '🌙' or '☀️'
        consumption_label: '夜间' or '日间'
    """
    try:
        snap_result = subprocess.run(
            [sys.executable, str(BALANCE_SCRIPT), "--snapshot", snapshot_label],
            capture_output=True, text=True, timeout=30,
        )
        if snap_result.returncode != 0:
            return None

        snapshots = load_snapshots()
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        # Find today's snapshot and the comparison snapshot (yesterday or earlier)
        today_snap = None
        comparison_snap = None
        for s in snapshots:
            ts = _parse_ts(s.get("timestamp", ""))
            label = s.get("label", "")
            if ts >= today_start and label == snapshot_label:
                today_snap = s
            elif ts < today_start and label == comparison_label:
                if comparison_snap is None or ts > _parse_ts(comparison_snap.get("timestamp", "")):
                    comparison_snap = s

        if not today_snap:
            return None

        consumption = None
        if comparison_snap:
            delta = comparison_snap["balance"] - today_snap["balance"]
            consumption = round(delta, 2)

        display = f"🐳 DeepSeek: ¥{today_snap['balance']:.2f}"
        if consumption is not None:
            if consumption >= 0:
                display += f" ({consumption_icon} {consumption_label} ¥{consumption:.2f})"
            else:
                display += f" ({consumption_icon} {consumption_label}充值 ¥{abs(consumption):.2f})"

        return {"balance": today_snap["balance"], "consumption": consumption, "display": display}
    except Exception:
        return None


def run(digest_type: str) -> dict:
    """Collect all digest data. Returns a dict (printed as JSON)."""
    config = load_config()

    # Determine which marker to read based on type
    if digest_type == "morning":
        marker_read = "evening"   # morning digest covers period since last evening
        marker_write = "morning"
        ds_snapshot = "morning"
        ds_comparison = "evening"
        ds_icon = "🌙"
        ds_label = "夜间"
    else:
        marker_read = "morning"   # evening digest covers period since last morning
        marker_write = "evening"
        ds_snapshot = "evening"
        ds_comparison = "morning"
        ds_icon = "☀️"
        ds_label = "日间"

    result = {
        "digest_type": digest_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "emails": {"total": 0, "by_account": {}, "classification": {}},
        "group_messages": {"total": 0, "messages": []},
        "todos": [],
        "escalation": {"should_escalate": False, "reasons": [], "details": {}},
        "deepseek": None,
        "errors": [],
    }

    # Step 1: Group messages
    try:
        since_ts = get_last_digest_timestamp(marker_read)
        result["since_timestamp"] = since_ts
        messages = fetch_group_messages(config, since_timestamp=since_ts)
        result["group_messages"] = {"total": len(messages), "messages": messages}
    except Exception as e:
        result["errors"].append(f"group_messages: {e}")
        messages = []

    # Step 2: Escalation
    try:
        result["escalation"] = check_escalation(messages, config)
    except Exception as e:
        result["errors"].append(f"escalation: {e}")

    # Step 3: Emails
    try:
        email_by_account = fetch_all_unread_emails(config)
        flat_emails = []
        for label, emails in email_by_account.items():
            for e in emails:
                e["account"] = label
            flat_emails.extend(emails)
        classification = classify_emails(flat_emails, config)
        result["emails"] = {
            "total": len(flat_emails),
            "by_account": {label: len(emails) for label, emails in email_by_account.items()},
            "classification": {
                "important": len(classification.get("important", [])),
                "normal": len(classification.get("normal", [])),
                "verification": len(classification.get("verification", [])),
                "junk": len(classification.get("junk", [])),
            },
            "details": classification,
        }
    except Exception as e:
        result["errors"].append(f"emails: {e}")

    # Step 4: Todos
    try:
        from graph_api import get_tasks
        result["todos"] = get_tasks(config)
    except Exception as e:
        result["errors"].append(f"todos: {e}")

    # Step 5: DeepSeek balance
    try:
        ds = compute_deepseek(ds_snapshot, ds_comparison, ds_icon, ds_label)
        if ds:
            result["deepseek"] = ds
        else:
            result["errors"].append("deepseek: snapshot not found after recording")
    except Exception as e:
        result["errors"].append(f"deepseek: {e}")

    # Step 6: Record marker
    try:
        set_last_digest_timestamp(marker_write)
    except Exception as e:
        result["errors"].append(f"timestamp_record: {e}")

    return result


def main():
    parser = argparse.ArgumentParser(description="Run digest pipeline")
    parser.add_argument("--type", required=True, choices=["morning", "evening"],
                        help="Digest type: morning or evening")
    args = parser.parse_args()
    result = run(args.type)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
