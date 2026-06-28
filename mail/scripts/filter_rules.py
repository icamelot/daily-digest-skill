"""Email classification and blacklist/important rule management."""
import json
import re
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config.json"
PREVIEW_LENGTH = 200

# Keywords that suggest a verification code email
_VERIFICATION_SUBJECT_KW = [
    "验证码", "verification code", "确认码", "安全码",
    "login code", "sign-in code", "one-time code", "otp",
    "security code", "授权码", "登录验证", "二次验证",
]
# Typical verification code patterns (4-8 digits, often isolated)
_VERIFICATION_CODE_RE = re.compile(
    r"(?<!\d)(\d{4,8})(?!\d)", re.MULTILINE
)


def _matches_keywords(text: str, keywords: list[str]) -> list[str]:
    """Return which keywords matched in text (case-insensitive)."""
    if not text or not keywords:
        return []
    text_lower = text.lower()
    return [kw for kw in keywords if kw.lower() in text_lower]


def _strip_html(text: str) -> str:
    """Remove HTML tags, CSS, and excessive whitespace for code extraction."""
    import re as _re
    # Remove style/script blocks
    text = _re.sub(r"<(style|script)[^>]*>.*?</\1>", " ", text, flags=_re.DOTALL | _re.IGNORECASE)
    # Remove HTML tags
    text = _re.sub(r"<[^>]+>", " ", text)
    # Collapse whitespace
    text = _re.sub(r"\s+", " ", text)
    return text.strip()


def extract_verification_code(email: dict) -> str | None:
    """
    Detect if an email contains a verification code and extract it.
    Checks subject keywords first, then scans body for numeric codes.
    Returns the code string or None.
    """
    subject = (email.get("subject") or "").lower()
    body = (email.get("body") or "")

    # Check if subject suggests verification
    has_verification_kw = any(kw in subject for kw in _VERIFICATION_SUBJECT_KW)
    if not has_verification_kw:
        return None

    # Strip HTML before searching for codes
    clean_body = _strip_html(body)

    # Extract potential codes from cleaned body
    matches = _VERIFICATION_CODE_RE.findall(clean_body[:2000])
    if matches:
        # Return the first plausible code (skip obvious years)
        for m in matches:
            if m not in ("2024", "2025", "2026", "2027"):
                return m
    return None


def classify_emails(emails: list[dict], config: dict) -> dict:
    """
    Classify emails into verification, important, normal, junk.
    Also detect frequency anomalies.

    Each email dict: {sender, subject, body, uid, date}
    Returns: {verification: [...], important: [...], normal: [...], junk: [...], anomalies: [str]}
    """
    filters = config.get("mail", {}).get("filters", {})
    important_senders = filters.get("important_senders", [])
    important_keywords = filters.get("important_keywords", [])
    blacklist_senders = filters.get("blacklist_senders", [])
    blacklist_keywords = filters.get("blacklist_keywords", [])

    verification = []
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

        # Verification code check (highest priority after blacklist)
        if extract_verification_code(email):
            verification.append(email)
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
        "verification": verification,
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
