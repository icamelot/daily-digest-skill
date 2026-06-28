"""Fetch unread emails via IMAP — supports DoH DNS and IMAP ID."""
import email
import imaplib
import json
import socket
import ssl
import urllib.request
from email.header import decode_header
from typing import Any

# Register IMAP ID command for providers that require it (163, QQ, etc.)
imaplib.Commands["ID"] = ("AUTH",)


def _resolve_host(hostname: str) -> str:
    """
    Resolve hostname via DNS-over-HTTPS (bypasses TUN DNS hijacking).
    Returns the first IPv4 address, or the hostname unchanged on failure.
    """
    try:
        url = f"https://dns.google/resolve?name={hostname}&type=A"
        resp = json.loads(urllib.request.urlopen(url, timeout=5).read())
        for answer in resp.get("Answer", []):
            if answer.get("type") == 1:
                return answer["data"]
    except Exception:
        pass
    return hostname


class _IMAP4_SSL_DoH(imaplib.IMAP4_SSL):
    """IMAP4_SSL subclass that resolves hostname via DoH for correct SNI."""

    def __init__(self, host="", port=993, *, ssl_context=None, timeout=None):
        self._sni_host = host
        real_ip = _resolve_host(host)
        super().__init__(real_ip, port, ssl_context=ssl_context, timeout=timeout)

    def _create_socket(self, timeout):
        sock = socket.create_connection((self.host, self.port), timeout)
        ctx = self.ssl_context or ssl.create_default_context()
        return ctx.wrap_socket(sock, server_hostname=self._sni_host)


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
    Uses DoH DNS + IMAP ID command for compatibility with 163/QQ.
    """
    conn = _IMAP4_SSL_DoH(imap_cfg["server"], imap_cfg["port"])
    try:
        conn.login(imap_cfg["username"], imap_cfg["password"])

        # Send IMAP ID — required by 163, harmless for others
        try:
            conn._simple_command(
                "ID",
                '("name" "Ductor" "version" "1.0" "vendor" "python")',
            )
        except imaplib.IMAP4.error:
            pass  # Some servers reject ID, ignore

        conn.select("INBOX")

        status, message_ids = conn.search(None, "UNSEEN")
        if status != "OK" or not message_ids[0]:
            return []

        emails = []
        for msg_id in message_ids[0].split():
            # Use BODY.PEEK to read without setting \Seen flag
            status, msg_data = conn.fetch(msg_id, "(BODY.PEEK[])")
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
