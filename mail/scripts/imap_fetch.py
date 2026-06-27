"""Fetch unread emails via IMAP."""
import email
import imaplib
from email.header import decode_header
from typing import Any


def _decode_header_value(value: Any) -> str:
    """Decode email header value to string."""
    if value is None:
        return ""
    parts = decode_header(value)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            charset = charset or "utf-8"
            try:
                decoded.append(part.decode(charset, errors="replace"))
            except (LookupError, UnicodeDecodeError):
                decoded.append(part.decode("utf-8", errors="replace"))
        else:
            decoded.append(str(part))
    return " ".join(decoded)


def _extract_body(msg: email.message.Message) -> str:
    """Extract plain text body from email message."""
    body_parts = []
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            if content_type == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    try:
                        body_parts.append(payload.decode(charset, errors="replace"))
                    except (LookupError, UnicodeDecodeError):
                        body_parts.append(payload.decode("utf-8", errors="replace"))
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            try:
                body_parts.append(payload.decode(charset, errors="replace"))
            except (LookupError, UnicodeDecodeError):
                body_parts.append(payload.decode("utf-8", errors="replace"))
    return "\n".join(body_parts)


def fetch_unread_emails_for_account(imap_cfg: dict) -> list[dict]:
    """
    Connect to a single IMAP account and fetch all unread emails.
    imap_cfg: {server, port, username, password}
    Returns list of dicts: {sender, subject, body, uid, date}
    """
    conn = imaplib.IMAP4_SSL(imap_cfg["server"], imap_cfg["port"])
    try:
        conn.login(imap_cfg["username"], imap_cfg["password"])
        conn.select("INBOX")

        status, message_ids = conn.search(None, "UNSEEN")
        if status != "OK" or not message_ids[0]:
            return []

        emails = []
        for msg_id in message_ids[0].split():
            status, msg_data = conn.fetch(msg_id, "(RFC822)")
            if status != "OK":
                continue

            for part in msg_data:
                if isinstance(part, tuple):
                    raw_email = part[1]
                    msg = email.message_from_bytes(raw_email)
                    emails.append({
                        "uid": msg_id.decode(),
                        "sender": _decode_header_value(msg.get("From", "")),
                        "subject": _decode_header_value(msg.get("Subject", "")),
                        "date": _decode_header_value(msg.get("Date", "")),
                        "body": _extract_body(msg),
                    })
                    break

        return emails
    finally:
        conn.logout()


def fetch_all_unread_emails(config: dict) -> dict[str, list[dict]]:
    """
    Fetch unread emails from all configured accounts.
    Returns dict: {account_label: [email_dicts]}
    Each email dict includes an extra 'account' field with the label.
    """
    accounts = config.get("mail", {}).get("accounts", [])
    result = {}
    for account in accounts:
        label = account.get("label", account["imap"]["username"])
        imap_cfg = account["imap"]
        try:
            emails = fetch_unread_emails_for_account(imap_cfg)
            for e in emails:
                e["account"] = label
            result[label] = emails
        except Exception as e:
            print(f"Failed to fetch from {label}: {e}")
            result[label] = []
    return result


def fetch_unread_emails(config: dict) -> list[dict]:
    """
    Legacy wrapper: fetch from all accounts and return flat list.
    Each email has an 'account' field.
    """
    all_emails = {}
    accounts = config.get("mail", {}).get("accounts", [])
    if accounts:
        all_emails = fetch_all_unread_emails(config)
    else:
        # Fallback: single-account old config format
        imap_cfg = config["mail"]["imap"]
        label = config["mail"].get("label", imap_cfg.get("username", "default"))
        all_emails[label] = fetch_unread_emails_for_account(imap_cfg)

    flat = []
    for account_label, emails in all_emails.items():
        for e in emails:
            e["account"] = account_label
        flat.extend(emails)
    return flat
