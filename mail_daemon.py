#!/usr/bin/env python3
"""Mail daemon — fetch, classify, push. Zero LLM.
Reuses imap_fetch + filter_rules from the skill. Replaces personal-mail cron."""
import json
import math
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SKILL_DIR / "mail" / "scripts"))

from imap_fetch import fetch_all_unread_emails
from filter_rules import classify_emails, _strip_html, extract_verification_code

CONFIG_PATH = SKILL_DIR / "config.json"
SEEN_FILE = SKILL_DIR / ".seen_uids.json"
UNPROCESSED_CLI = str(SKILL_DIR / "mail" / "scripts" / "unprocessed_mail.py")
MAX_SEEN_PER_ACCOUNT = 5000


def _load_env():
    for p in [
        os.path.expanduser("~/.ductor/.env"),
        "/ductor/.env",
    ]:
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


def _load_seen() -> dict:
    if not SEEN_FILE.exists():
        return {}
    with open(SEEN_FILE) as f:
        return json.load(f)


def _save_seen(seen: dict) -> None:
    trimmed = {}
    for account, uids in seen.items():
        trimmed[account] = uids[-MAX_SEEN_PER_ACCOUNT:] if len(uids) > MAX_SEEN_PER_ACCOUNT else uids
    with open(SEEN_FILE, "w") as f:
        json.dump(trimmed, f, ensure_ascii=False)


def _filter_new(all_emails: dict) -> dict:
    seen = _load_seen()
    new = {}
    for label, emails in all_emails.items():
        seen_uids = set(seen.get(label, []))
        fresh = [e for e in emails if e.get("uid") not in seen_uids]
        if fresh:
            new[label] = fresh
    return new


def _mark_seen(all_emails: dict) -> None:
    seen = _load_seen()
    for label, emails in all_emails.items():
        seen.setdefault(label, [])
        existing = set(seen[label])
        for e in emails:
            uid = e.get("uid", "")
            if uid and uid not in existing:
                seen[label].append(uid)
                existing.add(uid)
    _save_seen(seen)


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


def _resolve_passwords(config: dict) -> dict:
    for account in config.get("mail", {}).get("accounts", []):
        for key in ("imap", "smtp"):
            cfg = account.get(key, {})
            if "password_env" in cfg and "password" not in cfg:
                cfg["password"] = os.environ.get(cfg["password_env"], "")
    return config


def _format_and_send(emails: list[dict], config: dict) -> bool:
    result = classify_emails(emails, config)
    tg_token = os.environ.get("DUCTOR_TG_TOKEN", "")
    tg_chat_id = os.environ.get("DUCTOR_TG_CHAT_ID", "")
    if not tg_token or not tg_chat_id:
        print("[mail_daemon] Missing TG credentials", file=sys.stderr)
        return False

    lines = []
    imp_count = len(result.get("important", []))
    lines.append(f"📬 邮件 — {imp_count} 封重要")

    verifications = result.get("verification", [])
    if verifications:
        lines.append("\n🔐 验证码:")
        for i, e in enumerate(verifications, 1):
            code = extract_verification_code(e) or "未识别"
            lines.append(f"  {i}. [{e['account']}] {e['sender']}")
            lines.append(f"     🔑 {code}  {e.get('subject','')[:50]}")

    important = result.get("important", [])
    if important:
        lines.append("\n🔴 重要:")
        for i, e in enumerate(important, 1):
            lines.append(f"  {i}. [{e['account']}] {e['sender']} — {e.get('subject','')[:60]}")

    normal = result.get("normal", [])
    if normal:
        lines.append("\n📋 普通:")
        for i, e in enumerate(normal, 1):
            lines.append(f"  {i}. [{e['account']}] {e['sender']} — {e.get('subject','')[:60]}")

    junk_count = len(result.get("junk", []))
    if junk_count:
        lines.append(f"\n🗑 垃圾: {junk_count} 封")

    anomalies = result.get("anomalies", [])
    for a in anomalies:
        lines.append(f"\n{a}")

    keyboard = None
    if normal:
        keyboard = json.dumps({
            "inline_keyboard": [[
                {"text": "✅ 一键已读", "callback_data": "mail:mark-all-read"}
            ]]
        })

    payload = {"chat_id": tg_chat_id, "text": "\n".join(lines)}
    if keyboard:
        payload["reply_markup"] = keyboard

    url = f"https://api.telegram.org/bot{tg_token}/sendMessage"
    data = json.dumps(payload).encode()
    try:
        req = urllib.request.Request(url, data=data,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read()
            result = json.loads(body)
            if not result.get("ok", False):
                print(f"[mail_daemon] API error: {result.get('description', 'unknown')} (error_code={result.get('error_code', '?')})", file=sys.stderr)
            return result.get("ok", False)
    except Exception as e:
        print(f"[mail_daemon] Send failed: {e}", file=sys.stderr)
        return False


def main():
    _load_env()
    socket.setdefaulttimeout(15)

    if not CONFIG_PATH.exists():
        print(f"[mail_daemon] Config not found: {CONFIG_PATH}", file=sys.stderr)
        sys.exit(1)

    INTERVAL = 300  # 5 minutes, aligned to wall clock
    print(f"[mail_daemon] Started (interval={INTERVAL}s, clock-aligned)", file=sys.stderr)
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


if __name__ == "__main__":
    main()
