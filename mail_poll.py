#!/usr/bin/env python3
"""Mail poll daemon — fetch, dedup, and notify main agent of new emails."""
import json
import os
import re
import socket
import sys
import time
import urllib.request

# Prevent hangs on blocked/flaky IMAP connections
socket.setdefaulttimeout(15)

# Add skill scripts to path
SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(SKILL_DIR, "mail", "scripts"))

from imap_fetch import fetch_all_unread_emails
from filter_rules import classify_emails, _strip_html

CONFIG_PATH = os.path.join(SKILL_DIR, "config.json")
SEEN_FILE = os.path.join(SKILL_DIR, ".seen_uids.json")
MAX_SEEN_PER_ACCOUNT = 5000

# Inter-agent bus
AGENT_API = "http://127.0.0.1:8799/interagent/send"


# ── seen_uids management ──────────────────────────────────────────

def _load_seen() -> dict:
    if not os.path.exists(SEEN_FILE):
        return {}
    with open(SEEN_FILE) as f:
        data = json.load(f)
    return {k: v for k, v in data.items()}


def _save_seen(seen: dict) -> None:
    # Trim to MAX_SEEN_PER_ACCOUNT per account
    trimmed = {}
    for account, uids in seen.items():
        if len(uids) > MAX_SEEN_PER_ACCOUNT:
            trimmed[account] = uids[-MAX_SEEN_PER_ACCOUNT:]
        else:
            trimmed[account] = uids
    with open(SEEN_FILE, "w") as f:
        json.dump(trimmed, f, ensure_ascii=False)


def _filter_new(all_emails: dict) -> dict:
    """Return only emails whose UIDs haven't been seen before."""
    seen = _load_seen()
    new_emails = {}
    for label, emails in all_emails.items():
        seen_uids = set(seen.get(label, []))
        fresh = [e for e in emails if e.get("uid") not in seen_uids]
        if fresh:
            new_emails[label] = fresh
    return new_emails


def _mark_seen(all_emails: dict) -> None:
    """Record all current email UIDs as seen."""
    seen = _load_seen()
    for label, emails in all_emails.items():
        if label not in seen:
            seen[label] = []
        existing = set(seen[label])
        for e in emails:
            uid = e.get("uid", "")
            if uid and uid not in existing:
                seen[label].append(uid)
                existing.add(uid)
    _save_seen(seen)


# ── email preprocessing ───────────────────────────────────────────

def _extract_text_body(email_body: str) -> str:
    """Strip HTML/CSS, return clean plain text."""
    return _strip_html(email_body or "")


def _extract_attachments(email_raw: dict) -> list[dict]:
    """Return list of {filename, mime_type, size} for attachments.
    Currently extracts from multipart headers — callers pass the raw body.
    For simplicity, we scan for Content-Disposition: attachment patterns.
    """
    # This is a lightweight fallback.  Full MIME parsing is in imap_fetch.py.
    # We add attachment metadata during the fetch phase (see imap_fetch changes).
    return email_raw.get("attachments", [])


def _preprocess_email(email: dict) -> dict:
    """Clean up an email dict for agent consumption."""
    body = email.get("body", "")
    clean_body = _extract_text_body(body)
    attachments = _extract_attachments(email)
    return {
        "uid": email.get("uid", ""),
        "sender": email.get("sender", ""),
        "subject": email.get("subject", ""),
        "body": clean_body[:500],  # truncate for token efficiency
        "attachments": attachments,
        "account": email.get("account", ""),
    }


# ── agent communication ───────────────────────────────────────────

def _post_to_agent(emails: list[dict]) -> bool:
    """Send new email data to the main agent via inter-agent bus."""
    # Format message: one email per block
    lines = ["📬 新邮件轮询\n以下为本次新收到的邮件，请按 mail/SKILL.md 规则分类并输出摘要：\n"]
    for i, e in enumerate(emails, 1):
        lines.append(f"## 邮件 {i}")
        lines.append(f"发件人: {e['sender']}")
        lines.append(f"主题: {e['subject']}")
        if e.get("attachments"):
            att_list = ", ".join(
                f"{a.get('filename', '?')} ({a.get('size', '?')})"
                for a in e["attachments"]
            )
            lines.append(f"附件: {att_list}")
        lines.append(f"正文: {e['body']}")
        lines.append(f"账户: {e['account']}")
        lines.append(f"UID: {e['uid']}")
        lines.append("")

    message = "\n".join(lines)
    payload = json.dumps({
        "from": "mail-poll",
        "to": "main",
        "message": message,
    }).encode()

    try:
        req = urllib.request.Request(
            AGENT_API,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode())
            return result.get("success", False)
    except Exception as e:
        print(f"[mail_poll] Failed to reach agent API: {e}", file=sys.stderr)
        return False


# ── password resolution ───────────────────────────────────────────

def _resolve_passwords(config: dict) -> dict:
    """Resolve password_env keys to actual passwords from env vars."""
    for account in config.get("mail", {}).get("accounts", []):
        for key in ("imap", "smtp"):
            cfg = account.get(key, {})
            if "password_env" in cfg and "password" not in cfg:
                cfg["password"] = os.environ.get(cfg["password_env"], "")
    return config


# ── main loop ─────────────────────────────────────────────────────

def main():
    if not os.path.exists(CONFIG_PATH):
        print(f"[mail_poll] Config not found: {CONFIG_PATH}", file=sys.stderr)
        sys.exit(1)

    print("[mail_poll] Starting mail poll daemon (interval=300s)", file=sys.stderr)

    while True:
        try:
            with open(CONFIG_PATH) as f:
                config = json.load(f)
            config = _resolve_passwords(config)

            all_emails = fetch_all_unread_emails(config)

            # Determine new emails (UNSEEN - seen)
            new_emails = _filter_new(all_emails)

            total_new = sum(len(v) for v in new_emails.values())
            if total_new > 0:
                # Preprocess and flatten
                flat = []
                for label, emails in new_emails.items():
                    for e in emails:
                        e["account"] = label
                        flat.append(_preprocess_email(e))

                success = _post_to_agent(flat)
                if success:
                    print(f"[mail_poll] Sent {total_new} new email(s) to agent",
                          file=sys.stderr)

            # Mark all current UNSEEN as seen (so next poll won't re-report)
            _mark_seen(all_emails)

        except Exception as e:
            print(f"[mail_poll] Error in poll cycle: {e}", file=sys.stderr)

        time.sleep(300)


def mark_normal_read():
    """Mark all normal (non-important, non-verification) unread emails as read."""
    import imaplib as _imaplib
    with open(CONFIG_PATH) as f:
        config = json.load(f)
    config = _resolve_passwords(config)
    all_emails = fetch_all_unread_emails(config)

    flat = []
    for label, emails in all_emails.items():
        for e in emails:
            e["account"] = label
            flat.append(e)

    if not flat:
        print("无未读邮件")
        return

    result = classify_emails(flat, config)
    normal_uids = {e["uid"]: e["account"] for e in result.get("normal", [])}
    if not normal_uids:
        print("无普通邮件需要标记已读")
        return

    for account in config["mail"]["accounts"]:
        label = account["label"]
        imap_cfg = account["imap"]
        uids = [uid for uid, acc in normal_uids.items() if acc == label]
        if not uids:
            continue
        try:
            from imap_fetch import _IMAP4_SSL_DoH
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
            uid_list = ",".join(uids)
            conn.uid("STORE", uid_list, "+FLAGS", "(\\Seen)")
            conn.logout()
        except Exception as e:
            print(f"标记 {label} 失败: {e}")

    # Also remove read UIDs from seen file
    seen = _load_seen()
    for uid, acc in normal_uids.items():
        if acc in seen and uid in seen[acc]:
            seen[acc].remove(uid)
    _save_seen(seen)

    print(f"已标记 {len(normal_uids)} 封普通邮件为已读")


if __name__ == "__main__":
    if "--mark-normal-read" in sys.argv:
        mark_normal_read()
    else:
        main()
