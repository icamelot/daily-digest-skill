# Unprocessed Mail Tracking

**Date**: 2026-07-01
**Status**: design approved, awaiting plan

## Problem

`mail_daemon.py` pushes new emails directly to Telegram without AI. When the user
has a backlog of unhandled emails, the AI agent cannot answer "how many emails
are pending" or "summarize the backlog" — it has no visibility into what was
pushed and what was handled.

The user must reply to each individual email message, which doesn't scale.

## Solution

A persistent, lightweight JSON list of unprocessed emails maintained by the
Python daemon. The agent reads it for awareness; a CLI tool handles all writes.

### Data flow

```
IMAP UNSEEN
    │
    ▼
mail_daemon.py (every 5 min)
    │
    ├── .seen_uids.json        ← dedup: "already pushed"
    ├── Telegram push           ← format + send
    └── .unprocessed_emails.json ← ADD new entries

mail_daemon.py sync-on-poll:
    ── Check IMAP \Seen → remove entries already read externally

Agent:
    Read .unprocessed_emails.json  → situational awareness
    CLI --summary                  → quick count, zero token
    CLI --mark-done <uid>          → process one
    CLI --mark-all-done            → process all (IMAP + list)

User "Mark All Read" button:
    Agent receives callback → CLI --mark-all-done
```

### Files

| File | Purpose |
|------|---------|
| `skills/personal-assistant/.unprocessed_emails.json` | State file, JSON array |
| `skills/personal-assistant/mail/scripts/unprocessed_mail.py` | CLI tool (sole writer) |

### Entry schema (lightweight)

```json
{
  "uid": "61",
  "sender": "Dwayne McDaniel <dwayne.mcdaniel@gitguardian.com>",
  "subject": "Connect a source to icamelot workspace",
  "date": "Wed, 01 Jul 2026 11:45:04 +0000",
  "account": "北京大学邮箱"
}
```

No body, no attachments. Agent fetches full content via `imap_fetch.py` on demand.

### CLI interface

```bash
# Statistics (fast awareness, minimal token)
python3 mail/scripts/unprocessed_mail.py --summary
# → {"total": 12, "accounts": {"北京大学邮箱": 5, "QQ邮箱": 3, "网易163邮箱": 2, "Gmail": 2}}

# List lightweight entries
python3 mail/scripts/unprocessed_mail.py --list

# Mark one email as processed
python3 mail/scripts/unprocessed_mail.py --mark-done <uid>

# Mark all as processed (IMAP \Seen + clear list)
python3 mail/scripts/unprocessed_mail.py --mark-all-done

# Daemon: add new emails to the list
python3 mail/scripts/unprocessed_mail.py --add <json-payload>

# Daemon: sync — remove entries whose IMAP uid is now \Seen
python3 mail/scripts/unprocessed_mail.py --sync-seen
```

### mail_daemon.py changes

1. After successful Telegram push: call `unprocessed_mail.py --add` with the new
   email entries
2. Each poll cycle: call `unprocessed_mail.py --sync-seen` to remove entries
   that the user read in an external client
3. When the "mark all read" callback fires: the daemon (or agent) calls
   `--mark-all-done` which both sets IMAP `\Seen` and clears the list

### mail/SKILL.md changes

Update "一键已读" section:
- Replace `python3 mail_poll.py --mark-all-read`
- With `python3 mail/scripts/unprocessed_mail.py --mark-all-done`

### Agent usage pattern

```
User: "帮我总结积压的邮件"
  → agent: CLI --summary  → 12 pending
  → agent: CLI --list     → see subjects/senders
  → agent: generates summary, presents to user
  → user: "好的"
  → agent: CLI --mark-all-done
```

```
User: "邮件情况怎么样"
  → agent: Read .unprocessed_emails.json (file I/O, no process spawn)
  → agent: "你有 5 封待处理邮件，3 封北大、2 封 QQ"
```

### Why not file-only (no CLI)?

Agent's Write tool does full-file overwrite, not atomic append/delete. With daemon
adding entries and agent removing them concurrently, plain file writes would race.
A single CLI tool serializes all writes → no conflicts. Agent reads are read-only
and safe.

### Why not CLI-only (no file read)?

`--list` dumps everything into the agent's context, burning tokens when there are
many pending emails. Reading the file directly lets the agent inspect the list
without a subprocess. `--summary` provides the lightweight alternative when the
agent just needs a count.

### Boundary: what counts as "processed"?

- Agent executes `--mark-done` or `--mark-all-done` after user request
- User clicks Telegram "Mark All Read" button → agent calls `--mark-all-done`
- User reads email in external client → daemon `--sync-seen` detects IMAP \Seen → auto-removes

### Non-goals

- Tracking read/unread status beyond the unprocessed list (IMAP handles that)
- Storing email bodies in the state file
- Multi-user or multi-agent concurrent access (single agent owns the file)
- Replacing `.seen_uids.json` (it serves a different purpose: push dedup)
