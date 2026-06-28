"""Read Telegram group chat history — supports multiple groups."""
import json
from pathlib import Path
from datetime import datetime, timezone


def fetch_group_messages(
    config: dict,
    since_timestamp: float | None = None,
    group_label: str | None = None,
) -> list[dict]:
    """
    Fetch messages from configured Telegram group(s).

    If group_label is specified, only that group is queried.
    Otherwise, fetches from all configured groups.

    If since_timestamp is provided, only returns messages after that time.
    Returns list of dicts: {sender_name, text, timestamp, message_id, group_label}
    """
    groups = config.get("digest", {}).get("groups", [])
    if not groups:
        # Fallback: old single-group format
        groups = [{
            "label": "default",
            "chat_id": config["digest"].get("group_chat_id", 0)
        }]

    telegram_files_dir = (
        Path(__file__).resolve().parent.parent.parent.parent / "telegram_files"
    )

    if since_timestamp is None:
        since_timestamp = 0.0

    all_messages = []
    for group in groups:
        label = group.get("label", str(group.get("chat_id", "")))
        if group_label and label != group_label:
            continue

        chat_id = group["chat_id"]
        chat_file = telegram_files_dir / f"chat_{chat_id}.jsonl"
        if not chat_file.exists():
            continue

        with open(chat_file, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    msg_ts = msg.get("timestamp", 0)
                    if msg_ts >= since_timestamp:
                        all_messages.append({
                            "sender_name": msg.get("sender_name", "Unknown"),
                            "text": msg.get("text", ""),
                            "timestamp": msg_ts,
                            "message_id": msg.get("message_id", ""),
                            "group_label": label,
                        })
                except json.JSONDecodeError:
                    continue

    return all_messages


def get_last_digest_timestamp(digest_type: str) -> float:
    """
    Get the timestamp of the last digest run.
    digest_type: 'morning' or 'evening'
    """
    marker_file = (
        Path(__file__).resolve().parent.parent.parent
        / ".digest_markers"
        / f"{digest_type}_last_run.txt"
    )
    if marker_file.exists():
        try:
            return float(marker_file.read_text().strip())
        except (ValueError, OSError):
            pass
    return 0.0


def set_last_digest_timestamp(digest_type: str) -> None:
    """Record that a digest just ran."""
    marker_file = (
        Path(__file__).resolve().parent.parent.parent
        / ".digest_markers"
        / f"{digest_type}_last_run.txt"
    )
    marker_file.parent.mkdir(parents=True, exist_ok=True)
    marker_file.write_text(str(datetime.now(timezone.utc).timestamp()))


def get_last_digest_timestamp(digest_type: str) -> float:
    """
    Get the timestamp of the last digest run.
    digest_type: 'morning' or 'evening'
    """
    marker_file = (
        Path(__file__).resolve().parent.parent.parent
        / ".digest_markers"
        / f"{digest_type}_last_run.txt"
    )
    if marker_file.exists():
        try:
            return float(marker_file.read_text().strip())
        except (ValueError, OSError):
            pass
    return 0.0


def set_last_digest_timestamp(digest_type: str) -> None:
    """Record that a digest just ran."""
    marker_file = (
        Path(__file__).resolve().parent.parent.parent
        / ".digest_markers"
        / f"{digest_type}_last_run.txt"
    )
    marker_file.parent.mkdir(parents=True, exist_ok=True)
    marker_file.write_text(str(datetime.now(timezone.utc).timestamp()))
