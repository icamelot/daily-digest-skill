#!/usr/bin/env python3
"""Mail poll daemon — fetch, dedup, and notify main agent of new emails."""
import json
import os
import re
import socket
import sys
import time
import urllib.request

# Load .env secrets
_ENV_FILE = os.path.expanduser("~/.ductor/.env")
if os.path.exists(_ENV_FILE):
    with open(_ENV_FILE) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _, _val = _line.partition("=")
                _key = _key.strip()
                _val = _val.strip().strip('"').strip("'")
                if _key not in os.environ:
                    os.environ[_key] = _val

# Prevent hangs on blocked/flaky IMAP connections
socket.setdefaulttimeout(15)

# Add skill scripts to path
SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(SKILL_DIR, "mail", "scripts"))

from imap_fetch import fetch_all_unread_emails
from filter_rules import classify_emails, _strip_html, extract_verification_code

CONFIG_PATH = os.path.join(SKILL_DIR, "config.json")
SEEN_FILE = os.path.join(SKILL_DIR, ".seen_uids.json")
MAX_SEEN_PER_ACCOUNT = 5000

# Telegram direct send — credentials from environment
TG_TOKEN = os.environ.get("DUCTOR_TG_TOKEN", "")
TG_CHAT_ID = os.environ.get("DUCTOR_TG_CHAT_ID", "")
TG_API = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"


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


# ── classification & formatting ────────────────────────────────────

def _format_and_send(emails: list[dict], config: dict) -> bool:
    """Classify emails, format summary, and send directly to Telegram."""
    result = classify_emails(emails, config)

    lines = []
    imp_count = len(result.get("important", []))
    lines.append(f"📬 邮件轮询汇报 — {imp_count} 封重要邮件")

    verifications = result.get("verification", [])
    if verifications:
        lines.append("")
        lines.append("🔐 验证码邮件:")
        for i, e in enumerate(verifications, 1):
            code = extract_verification_code(e) or "未识别"
            lines.append(f"  {i}. [{e['account']}] {e['sender']}")
            lines.append(f"     🔑 验证码: {code}")
            lines.append(f"     📌 {e.get('subject', '')[:60]}")

    important = result.get("important", [])
    if important:
        lines.append("")
        lines.append("🔴 重要邮件:")
        for i, e in enumerate(important, 1):
            lines.append(f"  {i}. [{e['account']}] {e['sender']}")
            lines.append(f"     📌 {e.get('subject', '')[:60]}")

    normal = result.get("normal", [])
    if normal:
        lines.append("")
        lines.append("📋 普通邮件:")
        for i, e in enumerate(normal, 1):
            lines.append(f"  {i}. [{e['account']}] {e['sender']} — {e.get('subject', '')[:60]}")

    junk_count = len(result.get("junk", []))
    if junk_count:
        lines.append(f"\n🗑 垃圾邮件: {junk_count} 封")

    anomalies = result.get("anomalies", [])
    if anomalies:
        lines.append("")
        for a in anomalies:
            lines.append(a)

    text = "\n".join(lines)

    # Add inline keyboard for mark-read
    keyboard = None
    if normal:
        keyboard = json.dumps({
            "inline_keyboard": [[
                {"text": "✅ 一键已读", "callback_data": "mail:mark-normal-read"}
            ]]
        })

    payload = {
        "chat_id": TG_CHAT_ID,
        "text": text,
    }
    if keyboard:
        payload["reply_markup"] = keyboard

    data = json.dumps(payload).encode()
    try:
        req = urllib.request.Request(TG_API, data=data,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp_data = json.loads(resp.read().decode())
            return resp_data.get("ok", False)
    except Exception as e:
        print(f"[mail_poll] Telegram send failed: {e}", file=sys.stderr)
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

                success = _format_and_send(flat, config)
                if success:
                    print(f"[mail_poll] Sent {total_new} new email(s) to Telegram",
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

    total_marked = 0
    for account in config["mail"]["accounts"]:
        label = account["label"]
        imap_cfg = account["imap"]
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

            # Fetch all UNSEEN in this account, classify, mark normals
            status, msg_ids = conn.search(None, "UNSEEN")
            if status != "OK" or not msg_ids[0]:
                conn.logout()
                continue

            # Fetch headers for all unread to classify
            emails = []
            for msg_id in msg_ids[0].split():
                s, data = conn.fetch(msg_id, "(BODY.PEEK[HEADER])")
                if s != "OK":
                    continue
                for part in data:
                    if isinstance(part, tuple):
                        import email as _email
                        msg = _email.message_from_bytes(part[1])
                        emails.append({
                            "uid": msg_id.decode(),
                            "sender": msg.get("From", ""),
                            "subject": msg.get("Subject", "") or "",
                            "body": "",
                            "account": label,
                        })
                        break

            if not emails:
                conn.logout()
                continue

            result = classify_emails(emails, config)
            normal = result.get("normal", [])
            if normal:
                for e in normal:
                    try:
                        conn.store(e["uid"], "+FLAGS", "(\\Seen)")
                        total_marked += 1
                    except _imaplib.IMAP4.error:
                        pass

            conn.logout()
        except Exception as e:
            print(f"标记 {label} 失败: {e}")

    # Remove read emails from seen file
    seen = _load_seen()
    # Rebuild seen: remove entries that are now \Seen
    for account in config["mail"]["accounts"]:
        label = account["label"]
        if label in seen:
            # Keep only non-normal UIDs (we don't know which are which,
            # so clean sweep — next poll will re-record unseen ones)
            pass  # seen_uids will naturally phase out as emails become \Seen

    print(f"已标记 {total_marked} 封普通邮件为已读")


if __name__ == "__main__":
    if "--mark-normal-read" in sys.argv:
        mark_normal_read()
    else:
        main()
