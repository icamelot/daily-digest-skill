"""Email classification and blacklist/important rule management."""
import json
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config.json"
PREVIEW_LENGTH = 200


def _matches_keywords(text: str, keywords: list[str]) -> list[str]:
    """Return which keywords matched in text (case-insensitive)."""
    if not text or not keywords:
        return []
    text_lower = text.lower()
    return [kw for kw in keywords if kw.lower() in text_lower]


def classify_emails(emails: list[dict], config: dict) -> dict:
    """
    Classify emails into important, normal, junk.
    Also detect frequency anomalies.

    Each email dict: {sender, subject, body, uid, date}
    Returns: {important: [...], normal: [...], junk: [...], anomalies: [str]}
    """
    filters = config.get("mail", {}).get("filters", {})
    important_senders = filters.get("important_senders", [])
    important_keywords = filters.get("important_keywords", [])
    blacklist_senders = filters.get("blacklist_senders", [])
    blacklist_keywords = filters.get("blacklist_keywords", [])

    important = []
    normal = []
    junk = []
    keyword_hits: dict[str, int] = {}

    for email in emails:
        sender = email.get("sender", "")
        subject = email.get("subject", "")
        body_preview = (email.get("body", "") or "")[:PREVIEW_LENGTH]
        search_text = f"{sender} {subject} {body_preview}"

        # Blacklist check first (takes priority)
        if sender in blacklist_senders:
            junk.append(email)
            continue
        if _matches_keywords(search_text, blacklist_keywords):
            junk.append(email)
            continue

        # Track keyword frequency for anomaly detection
        all_kw = important_keywords + blacklist_keywords
        matched = _matches_keywords(search_text, all_kw)
        for kw in matched:
            keyword_hits[kw] = keyword_hits.get(kw, 0) + 1

        # Important check
        is_important = False
        if sender in important_senders:
            is_important = True
        if _matches_keywords(search_text, important_keywords):
            is_important = True

        if is_important:
            important.append(email)
        else:
            normal.append(email)

    # Frequency anomaly detection
    anomalies = []
    total = len(emails)
    if total > 0:
        for kw, count in keyword_hits.items():
            ratio = count / total
            if ratio > 0.5:
                anomalies.append(
                    f"⚠️ 关键词 '{kw}' 在本次邮件中占比异常（{count}/{total}），请注意。"
                )

    return {
        "important": important,
        "normal": normal,
        "junk": junk,
        "anomalies": anomalies,
    }


def _update_config_list(key: str, value: str) -> None:
    """Append a value to a list in the config file."""
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Config not found: {CONFIG_PATH}")
    config = json.loads(CONFIG_PATH.read_text())
    target = config.setdefault("mail", {}).setdefault("filters", {}).setdefault(key, [])
    if value not in target:
        target.append(value)
        CONFIG_PATH.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n")


def add_to_blacklist(target_type: str, value: str) -> None:
    """
    Add to blacklist. target_type: 'sender' or 'keyword'.
    Writes directly to config.json.
    """
    key = f"blacklist_{target_type}s"
    _update_config_list(key, value)


def add_to_important(target_type: str, value: str) -> None:
    """
    Mark as important. target_type: 'sender' or 'keyword'.
    Writes directly to config.json.
    """
    key = f"important_{target_type}s"
    _update_config_list(key, value)
