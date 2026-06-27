# Personal Evening Digest

## Goal

Generate evening digest with group messages recap, email overview, and todos with tomorrow preview.

## Assignment

Load the personal-assistant skill. Generate the evening digest:
1. Call digest/scripts/group_reader.get_last_digest_timestamp('morning') to get since-timestamp, then fetch_group_messages(config, since_ts).
2. Call digest/scripts/escalate.check_escalation(messages, config).
3. Fetch email overview via mail module.
4. Fetch todo status via todo module with tomorrow preview.
5. Output structured digest card with expand buttons. If escalation triggered, output detailed per-agent breakdown.
6. After done, call group_reader.set_last_digest_timestamp('evening').

## Output

Structured digest card with expand buttons. If escalation triggered, include a detailed per-agent breakdown.
