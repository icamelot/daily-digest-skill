# Personal Mail Poll

## Goal

Poll IMAP inbox for new emails, classify via filter rules, surface important ones to user immediately.

## Assignment

Load the personal-assistant skill. Call mail/scripts/imap_fetch.fetch_unread_emails(config), then mail/scripts/filter_rules.classify_emails(emails, config). If any important emails found, push them to the user immediately with a summary card. If frequency anomalies detected, append the alert.

## Output

Push a structured summary card to the user via Telegram for any important emails found. Include sender, subject, and a brief preview. If no important emails found, complete silently (no output needed).
