# Daily Digest Skill

A Telegram-based AI assistant skill that manages email, produces daily group chat digests, and manages Microsoft To Do tasks. Built for the Ductor multi-agent framework.

## Features

- 📧 **Email** — IMAP/SMTP. Smart filter (important / normal / spam), one-click blacklist, frequency anomaly alerts, draft and send replies
- 📊 **Daily Digest** — Twice daily (8:00 / 22:00). Group chat recap + email overview + todo status. Tiered depth with auto-escalation
- ✅ **To Do** — Microsoft Graph API. Create, update, complete tasks. Proactive completion detection

## Install

1. Copy this directory into your Ductor workspace under `skills/`:

   ```
   skills/personal-assistant/
   ```

2. Copy and fill in your config:

   ```bash
   cp config.example.json config.json
   # Edit config.json with your IMAP/SMTP server, Microsoft Graph credentials, etc.
   ```

3. Set secrets in `~/.ductor/.env`:

   ```env
   MAIL_PASSWORD=your-imap-password
   MS_CLIENT_ID=your-azure-ad-client-id
   MS_CLIENT_SECRET=your-azure-ad-client-secret
   MS_TENANT_ID=your-azure-ad-tenant-id
   ```

4. Set up cron jobs for mail polling and daily digests.

## Usage

Chat with the assistant via Telegram:

```
查邮件
日报
我有什么待办？
帮我回复那封邮件，大意是...
标记 "提交报告" 为完成
拉黑发件人 spam@example.com
```

## Config

See `config.example.json` for all settings. Secrets use the `_env` suffix and are resolved from `~/.ductor/.env` at runtime.

## Structure

```
├── SKILL.md
├── config.example.json
├── mail/
│   ├── SKILL.md
│   └── scripts/
│       ├── imap_fetch.py
│       ├── smtp_send.py
│       └── filter_rules.py
├── digest/
│   ├── SKILL.md
│   └── scripts/
│       ├── group_reader.py
│       └── escalate.py
├── todo/
│   ├── SKILL.md
│   └── scripts/
│       └── graph_api.py
├── shared/
│   └── utils.py
└── tests/
    ├── test_filter_rules.py
    └── test_escalate.py
```

## Dependencies

- Python 3.10+
- `requests` (for Microsoft Graph API)
- Ductor multi-agent framework
- Telegram Bot API (via Ductor)
- IMAP/SMTP server access
- Microsoft 365 work/school account (Azure AD app registration for To Do)

## License

MIT
