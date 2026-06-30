#!/bin/bash
# Start background daemons (zero-AI replacements for personal-mail + user-notification-watch crons)
# Called on Ductor startup or manually.

SKILL_DIR="$(cd "$(dirname "$0")" && pwd)"

# Kill existing daemons to avoid duplicates
pkill -f "mail_daemon.py" 2>/dev/null || true
pkill -f "notification_broker.py" 2>/dev/null || true

sleep 1

# Start daemons
cd "$SKILL_DIR"
nohup python3 mail_daemon.py >> /tmp/mail_daemon.log 2>&1 &
echo "mail_daemon PID=$!"

nohup python3 notification_broker.py >> /tmp/notification_broker.log 2>&1 &
echo "notification_broker PID=$!"
