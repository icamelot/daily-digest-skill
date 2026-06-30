#!/usr/bin/env python3
"""Notification broker — polls shared queue, pushes to user via Telegram.
Zero LLM. Intended to replace user-notification-watch cron."""
import json
import os
import sys
import time
import urllib.request
from pathlib import Path


def _load_env(paths: list[str]) -> None:
    for p in paths:
        if os.path.exists(p):
            with open(p) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, _, v = line.partition("=")
                    k, v = k.strip(), v.strip().strip('"').strip("'")
                    if k and not k.startswith("export"):
                        os.environ[k] = v


def _send_telegram(chat_id: str, text: str, token: str) -> bool:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({"chat_id": chat_id, "text": text}).encode()
    req = urllib.request.Request(url, data=payload,
                                  headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read()).get("ok", False)
    except Exception as e:
        print(f"[broker] Send failed: {e}", file=sys.stderr)
        return False


def _resolve_queue_path() -> Path:
    p = Path(os.environ.get("DUCTOR_HOME", Path.home() / ".ductor"))
    if p.parent.name == "agents":
        p = p.parent.parent
    return p / ".user_notification_queue.json"


def _audit_hook(entry: dict) -> bool:
    """Future: main-agent approval gate. Currently always passes."""
    return True


def main():
    _load_env([
        os.path.expanduser("~/.ductor/.env"),
        "/ductor/.env",
    ])
    token = os.environ.get("DUCTOR_TG_TOKEN", "")
    chat_id = os.environ.get("DUCTOR_TG_CHAT_ID", "")
    if not token or not chat_id:
        print("[broker] Missing DUCTOR_TG_TOKEN or DUCTOR_TG_CHAT_ID", file=sys.stderr)
        sys.exit(1)

    print("[broker] Started (interval=300s)", file=sys.stderr)
    while True:
        try:
            queue_path = _resolve_queue_path()
            if not queue_path.exists():
                time.sleep(300)
                continue

            entries = json.loads(queue_path.read_text())
            if not isinstance(entries, list) or not entries:
                time.sleep(300)
                continue

            remaining = []
            for entry in entries:
                if not _audit_hook(entry):
                    remaining.append(entry)
                    continue
                sender = entry.get("from", "unknown")
                text = entry.get("text", "")
                if not text:
                    continue
                prefix = f"F514 来自 {sender} 的通知:\n\n"
                if _send_telegram(chat_id, prefix + text, token):
                    print(f"[broker] Sent notification from {sender}", file=sys.stderr)
                else:
                    remaining.append(entry)

            if remaining:
                queue_path.write_text(json.dumps(remaining, ensure_ascii=False))
            else:
                queue_path.unlink(missing_ok=True)

        except Exception as e:
            print(f"[broker] Error: {e}", file=sys.stderr)

        time.sleep(300)


if __name__ == "__main__":
    main()
