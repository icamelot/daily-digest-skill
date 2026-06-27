"""Detect when a digest should escalate from lightweight to detailed."""
from collections import Counter


def check_escalation(messages: list[dict], config: dict) -> dict:
    """
    Check escalation triggers against a set of group messages.

    Returns:
        {should_escalate: bool, reasons: [str], details: dict}
    """
    keywords = config.get("digest", {}).get("escalation_keywords", [])
    reasons = []
    details = {}

    if not messages:
        return {"should_escalate": False, "reasons": [], "details": {}}

    # 1. Check for escalation keywords
    keyword_hits = []
    for msg in messages:
        text = msg.get("text", "")
        for kw in keywords:
            if kw.lower() in text.lower():
                keyword_hits.append({
                    "keyword": kw,
                    "sender": msg.get("sender_name"),
                    "snippet": text[:100],
                })
    if keyword_hits:
        unique_kws = list(set(h["keyword"] for h in keyword_hits))
        reasons.append(f"群聊中出现敏感关键词: {', '.join(unique_kws)}")
        details["keyword_hits"] = keyword_hits

    # 2. Check for agent silence (>6 hours — flagged if only 1 sender is active)
    sender_counts = Counter(msg.get("sender_name") for msg in messages)
    all_senders = list(sender_counts.keys())
    if len(all_senders) == 1 and len(messages) > 20:
        reasons.append(f"只有 {all_senders[0]} 在发言，其他 agent 可能异常静默")
        details["silent_senders_note"] = f"Only {all_senders[0]} active"

    # 3. Check for topic looping (>3 rounds on same topic)
    # Heuristic: repeated similar short messages
    short_texts = [
        msg.get("text", "")[:50]
        for msg in messages
        if len(msg.get("text", "")) > 10
    ]
    text_counter = Counter(short_texts)
    repeated = [(text, count) for text, count in text_counter.items() if count > 3]
    if repeated:
        top_repeat = repeated[0]
        reasons.append(
            f"疑似话题重复讨论: '{top_repeat[0]}...' 出现 {top_repeat[1]} 次"
        )
        details["repeated_topics"] = repeated

    return {
        "should_escalate": len(reasons) > 0,
        "reasons": reasons,
        "details": details,
    }
