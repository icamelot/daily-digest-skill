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


def fetch_unread_emails(config: dict) -> list[dict]:
    """
    Connect to IMAP server and fetch all unread emails.
    Returns list of dicts: {sender, subject, body, uid, date}
    """
    mail_config = config["mail"]
    imap_cfg = mail_config["imap"]

    conn = imaplib.IMAP4_SSL(imap_cfg["server"], imap_cfg["port"])
    try:
        conn.login(imap_cfg["username"], imap_cfg["password"])
        conn.select("INBOX")

        # Search for unread messages
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
