#!/usr/bin/env python3
"""Unprocessed email state — CLI for agent and daemon."""
import json
import os
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_STATE = SKILL_DIR / ".unprocessed_emails.json"

KEEP_KEYS = {"uid", "sender", "subject", "date", "account"}


def _state_path() -> Path:
    """Resolve path, allowing override via env for testing."""
    override = os.environ.get("_UNPROCESSED_TEST_STATE", "")
    if override:
        return Path(override)
    return DEFAULT_STATE


def _load() -> list[dict]:
    """Read and validate the state file. Returns empty list on any error."""
    path = _state_path()
    if not path.exists():
        return []
    try:
        with open(path) as f:
            data = json.load(f)
        if not isinstance(data, list):
            return []
        return data
    except (json.JSONDecodeError, OSError):
        return []


def _save(entries: list[dict]) -> None:
    """Atomic-ish write: write to temp then rename."""
    path = _state_path()
    tmp = path.with_suffix(path.suffix + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(tmp, "w") as f:
        json.dump(entries, f, ensure_ascii=False)
    tmp.rename(path)


def _strip_extra_keys(entry: dict) -> dict:
    """Keep only the keys defined in KEEP_KEYS."""
    return {k: entry.get(k, "") for k in KEEP_KEYS}


# ── commands ──────────────────────────────────────────────────────────

def cmd_summary():
    entries = _load()
    accounts = {}
    for e in entries:
        acct = e.get("account", "unknown")
        accounts[acct] = accounts.get(acct, 0) + 1
    result = {"total": len(entries), "accounts": accounts}
    print(json.dumps(result, ensure_ascii=False))


def cmd_list():
    entries = _load()
    print(json.dumps(entries, ensure_ascii=False))


def cmd_add():
    """Read JSON array from stdin, append to state (dedup by uid)."""
    raw = sys.stdin.read()
    try:
        incoming = json.loads(raw)
        if not isinstance(incoming, list):
            print("Error: stdin must be a JSON array", file=sys.stderr)
            sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: invalid JSON on stdin: {e}", file=sys.stderr)
        sys.exit(1)

    entries = _load()
    existing_uids = {e["uid"] for e in entries}

    for item in incoming:
        uid = item.get("uid", "")
        if uid and uid not in existing_uids:
            entries.append(_strip_extra_keys(item))
            existing_uids.add(uid)

    _save(entries)
    print(json.dumps({"added": len(incoming), "total": len(entries)}))


def cmd_mark_done(uid: str):
    entries = _load()
    before = len(entries)
    entries = [e for e in entries if e.get("uid") != uid]
    _save(entries)
    print(json.dumps({"removed": before - len(entries), "total": len(entries)}))


def cmd_mark_all_done():
    """Mark all IMAP UNSEEN as \\Seen AND clear the unprocessed list."""
    config = _load_config()
    config = _resolve_passwords(config)

    total_marked = _imap_mark_all_seen(config)

    # Clear the list
    entries = _load()
    count = len(entries)
    _save([])

    print(json.dumps({
        "imap_marked_seen": total_marked,
        "unprocessed_cleared": count,
    }))


def cmd_sync_seen():
    """Remove entries whose IMAP uid is no longer UNSEEN (read externally)."""
    config = _load_config()
    config = _resolve_passwords(config)

    unseen_uids = _imap_get_unseen_uids(config)

    entries = _load()
    before = len(entries)
    entries = [e for e in entries if e.get("uid") in unseen_uids]
    after = len(entries)
    _save(entries)

    print(json.dumps({"removed": before - after, "total": after}))


# ── IMAP helpers (reuse existing patterns) ────────────────────────────

def _load_config() -> dict:
    config_path = SKILL_DIR / "config.json"
    if not config_path.exists():
        print("Error: config.json not found", file=sys.stderr)
        sys.exit(1)
    with open(config_path) as f:
        return json.load(f)


def _resolve_passwords(config: dict) -> dict:
    for account in config.get("mail", {}).get("accounts", []):
        for key in ("imap", "smtp"):
            cfg = account.get(key, {})
            if "password_env" in cfg and "password" not in cfg:
                cfg["password"] = os.environ.get(cfg["password_env"], "")
    return config


def _imap_get_unseen_uids(config: dict) -> set[str]:
    """Return set of uid strings that are currently UNSEEN across all accounts."""
    import imaplib as _imaplib
    imaplib.Commands["ID"] = ("AUTH",)

    # Add mail/scripts to path for _IMAP4_SSL_DoH
    mail_scripts = str(Path(__file__).resolve().parent)
    if mail_scripts not in sys.path:
        sys.path.insert(0, mail_scripts)

    from imap_fetch import _IMAP4_SSL_DoH

    uids = set()
    for account in config.get("mail", {}).get("accounts", []):
        imap_cfg = account.get("imap", {})
        if not imap_cfg.get("password"):
            continue
        try:
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
            status, msg_ids = conn.search(None, "UNSEEN")
            if status == "OK" and msg_ids[0]:
                for mid in msg_ids[0].split():
                    uids.add(mid.decode())
            conn.logout()
        except Exception as e:
            print(f"sync-seen: failed {account.get('label','?')}: {e}", file=sys.stderr)
    return uids


def _imap_mark_all_seen(config: dict) -> int:
    """Mark all UNSEEN as \\Seen across all accounts. Returns count."""
    import imaplib as _imaplib
    imaplib.Commands["ID"] = ("AUTH",)

    mail_scripts = str(Path(__file__).resolve().parent)
    if mail_scripts not in sys.path:
        sys.path.insert(0, mail_scripts)

    from imap_fetch import _IMAP4_SSL_DoH

    total = 0
    for account in config.get("mail", {}).get("accounts", []):
        label = account.get("label", "?")
        imap_cfg = account.get("imap", {})
        if not imap_cfg.get("password"):
            continue
        try:
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
            status, msg_ids = conn.search(None, "UNSEEN")
            if status == "OK" and msg_ids[0]:
                for mid in msg_ids[0].split():
                    try:
                        conn.store(mid.decode(), "+FLAGS", "(\\Seen)")
                        total += 1
                    except _imaplib.IMAP4.error:
                        pass
            conn.logout()
        except Exception as e:
            print(f"mark-all-done: failed {label}: {e}", file=sys.stderr)
    return total


# ── CLI dispatch ──────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: unprocessed_mail.py --summary|--list|--add|--mark-done|--mark-all-done|--sync-seen",
              file=sys.stderr)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "--summary":
        cmd_summary()
    elif cmd == "--list":
        cmd_list()
    elif cmd == "--add":
        cmd_add()
    elif cmd == "--mark-done":
        if len(sys.argv) < 3:
            print("Usage: unprocessed_mail.py --mark-done <uid>", file=sys.stderr)
            sys.exit(1)
        cmd_mark_done(sys.argv[2])
    elif cmd == "--mark-all-done":
        cmd_mark_all_done()
    elif cmd == "--sync-seen":
        cmd_sync_seen()
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
